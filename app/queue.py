import random
import string
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict
from uuid import UUID, uuid4

from flask import current_app
from redis import Redis

from app.aws.metrics import (
    put_batch_saving_expiry_metric,
    put_batch_saving_inflight_metric,
    put_batch_saving_inflight_processed,
    put_batch_saving_metric,
)
from app.aws.metrics_logger import MetricsLogger


def generate_element(length=10) -> str:
    elem = "".join(random.choice(string.ascii_lowercase) for i in range(length))
    return elem


def generate_elements(count=10) -> list[str]:
    return [generate_element(count) for s in range(count)]


class Buffer(Enum):
    INBOX = "inbox"
    IN_FLIGHT = "in-flight"

    def inbox_name(self, suffix=None, process_type=None):
        if process_type and suffix:
            return f"{self.value}:{suffix}:{process_type}"
        if suffix:
            return f"{self.value}:{suffix}"
        if process_type:
            # Added two ":" to keep the same format as suffix:process_type
            return f"{self.value}::{str(process_type)}"
        return self.value

    def inflight_prefix(self, suffix: str = None, process_type: str = None) -> str:
        if process_type and suffix:
            return f"{Buffer.IN_FLIGHT.value}:{str(suffix)}:{str(process_type)}"
        if suffix:
            return f"{Buffer.IN_FLIGHT.value}:{str(suffix)}"
        if process_type:
            # Added two ":" to keep the same format as suffix:process_type
            return f"{Buffer.IN_FLIGHT.value}::{str(process_type)}"
        return f"{Buffer.IN_FLIGHT.value}"

    def inflight_name(self, receipt: UUID = uuid4(), suffix: str = None, process_type: str = None) -> str:
        return f"{self.inflight_prefix(suffix, process_type)}:{str(receipt)}"


class Queue(ABC):
    """Queue interface for custom buffer.

    Implementations should allow to poll from the queue and acknowledge
    read messages once work is done on these.
    """

    @abstractmethod
    def poll(self, count=10) -> tuple[UUID, list[str]]:
        """Gets messages out of the queue.

        Each polling is associated with a UUID acting as a receipt. This
        can later be used in conjunction with the `acknowledge` function
        to confirm that the polled messages were properly processed.
        This will delete the in-flight messages and these will not get
        back into the main inbox. Failure to achknowledge the polled
        messages will get these back into the inbox after a preconfigured
        timeout has passed, ready to be retried.

        Args:
            count (int, optional): Number of messages to get out of the queue. Defaults to 10.

        Returns:
            tuple[UUID, list[str]]: Gets polling receipt and list of polled notifications.
        """
        pass

    @abstractmethod
    def acknowledge(self, receipt: UUID):
        """Acknowledges reception and processing of provided messages IDs.

        Once the acknowledgement is done, the messages will get their in-flight
        status removed and will not get served again through the `poll` method.

        Args:
            message_ids (list[int]): [description]
        """
        pass

    @abstractmethod
    def publish(self, message: str):
        """Publishes the message into the buffer queue.

        The message is put onto the back of the queue to be later processed
        in a FIFO order.

        Args:
            message (str): Message to store into the queue.
        """
        pass


# TODO: Check if we want to move the queue API and implementations into the utils project.
class RedisQueue(Queue):
    """Implementation of a queue using Redis."""

    LUA_MOVE_TO_INFLIGHT = "move-in-inflight"
    LUA_EXPIRE_INFLIGHTS = "expire-inflights"

    scripts: Dict[str, Any] = {}

    def __init__(self, suffix=None, expire_inflight_after_seconds=300, process_type=None) -> None:
        """
        Constructor for the Redis Queue

        suffix: str
            Suffix can be of type "inbox" or "in-flight". Defines what type of Redis list is created
        expire_inflight_after_seconds: int
            Seconds indicating how long an in-flight list should be kept around before being sent to
            the inbox
        process_type: str
            String indicating the priority of the notification. It can be one of "priority", "bulk" or "normal"

        Return:
        -------
        RedisQueue

        """
        self._inbox = Buffer.INBOX.inbox_name(suffix, process_type)
        self._suffix = suffix
        self._process_type = process_type
        self._expire_inflight_after_seconds = expire_inflight_after_seconds

    def init_app(self, redis: Redis, metrics_logger: MetricsLogger):
        self._redis_client = redis
        self.__register_scripts()
        self.__metrics_logger = metrics_logger

    def poll(self, count=10) -> tuple[UUID, list[str]]:
        receipt = uuid4()
        in_flight_key = Buffer.IN_FLIGHT.inflight_name(receipt, self._suffix, self._process_type)
        results = self.__move_to_inflight(in_flight_key, count)
        if results:
            current_app.logger.info(f"Inflight created: {in_flight_key}")
            put_batch_saving_inflight_metric(self.__metrics_logger, self, 1)
        return (receipt, results)

    def expire_inflights(self):
        if self._process_type:
            args = [
                f"{Buffer.IN_FLIGHT.inflight_prefix()}:{self._suffix}:{self._process_type}*",
                self._inbox,
                self._expire_inflight_after_seconds,
            ]
        else:
            args = [f"{Buffer.IN_FLIGHT.inflight_prefix()}:{self._suffix}*", self._inbox, self._expire_inflight_after_seconds]
        expired = self.scripts[self.LUA_EXPIRE_INFLIGHTS](args=args)
        if expired:
            put_batch_saving_expiry_metric(self.__metrics_logger, self, len(expired))
            current_app.logger.warning(f"Moved inflights {expired} back to inbox {self._inbox}")

    def acknowledge(self, receipt: UUID) -> bool:
        """
        Remove the in-flight list from Redis

        Args:
        receipt: UUID
            id of the inflight to remove

        Returns: True if the inflight was found in that queue and removed, False otherwise
        """
        inflight_name = Buffer.IN_FLIGHT.inflight_name(receipt, self._suffix, self._process_type)
        if not self._redis_client.exists(inflight_name):
            current_app.logger.warning(f"Inflight to delete not found: {inflight_name}")
            return False
        self._redis_client.delete(inflight_name)
        current_app.logger.info(f"Acknowleged inflight: {inflight_name}")
        put_batch_saving_inflight_processed(self.__metrics_logger, self, 1)
        return True

    def publish(self, message: str):
        self._redis_client.rpush(self._inbox, message)
        put_batch_saving_metric(self.__metrics_logger, self, 1)

    def __move_to_inflight(self, in_flight_key: str, count: int) -> list[str]:
        results = self.scripts[self.LUA_MOVE_TO_INFLIGHT](args=[self._inbox, in_flight_key, count])
        decoded = [result.decode("utf-8") for result in results]
        return decoded

    def __register_scripts(self):
        self.scripts[self.LUA_MOVE_TO_INFLIGHT] = self._redis_client.register_script(
            """
            local DEFAULT_CHUNK = 99

            local source        = ARGV[1]
            local destination   = ARGV[2]
            local source_size   = tonumber(redis.call("LLEN", source))
            local count         = math.min(source_size, tonumber(ARGV[3]))

            local chunk_size    = math.min(math.max(0, count-1), DEFAULT_CHUNK)
            local current       = 0
            local all           = {}

            while current < count do
                local elements = redis.call("LRANGE", source, 0, chunk_size)
                redis.call("LPUSH", destination, unpack(elements))
                redis.call("LTRIM", source, chunk_size+1, -1)
                for i=1,#elements do all[#all+1] = elements[i] end

                current    = current + chunk_size+1
                chunk_size = math.min((count-1) - current, DEFAULT_CHUNK)
            end

            return all
            """
        )

        self.scripts[self.LUA_EXPIRE_INFLIGHTS] = self._redis_client.register_script(
            """
            local DEFAULT_CHUNK   = 99
            local inflight_prefix = ARGV[1]
            local destination     = ARGV[2]
            local expire_after    = tonumber(ARGV[3])

            local cursor = "0";
            local expired_inflights = {}
            repeat
                local scan_result = redis.call("SCAN", cursor, "MATCH", inflight_prefix, "COUNT", 100);
                cursor = scan_result[1]
                for i, inflight in pairs(scan_result[2]) do
                    local idle = redis.call("object", "idletime", inflight)
                    if ( idle > expire_after) then
                        local count         = tonumber(redis.call("LLEN", inflight))
                        local chunk_size    = math.min(math.max(0, count-1), DEFAULT_CHUNK)
                        local current       = 0

                        while current < count do
                            local elements = redis.call("LRANGE", inflight, 0, chunk_size)
                            redis.call("LPUSH", destination, unpack(elements))
                            redis.call("LTRIM", inflight, chunk_size+1, -1)
                            current    = current + chunk_size+1
                            chunk_size = math.min((count-1) - current, DEFAULT_CHUNK)
                        end

                        expired_inflights[#expired_inflights+1] = inflight
                        redis.call("del", inflight)
                    end
                end
            until cursor == "0";
            return expired_inflights
            """
        )


class MockQueue(Queue):
    """Implementation of a queue that spits out randomly generated elements.

    Do not use in production!"""

    def poll(self, count=10) -> tuple[UUID, list[str]]:
        receipt = uuid4()
        return (receipt, generate_elements(count))

    def acknowledge(self, receipt: UUID):
        pass

    def publish(self, message: str):
        pass
