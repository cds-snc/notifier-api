import time
from contextlib import contextmanager
from os import getenv
from unittest import mock
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from flask import Flask
from pytest_mock_resources import RedisConfig, create_redis_fixture

from app import create_app, flask_redis, metrics_logger
from app.config import Config, Test
from app.queue import Buffer, MockQueue, RedisQueue, generate_element


@pytest.fixture(scope="session")
def pmr_redis_config():
    parsed_uri = urlparse(getenv("REDIS_URL"))
    return RedisConfig(image="redis:6.2", host=parsed_uri.hostname, port="6380", ci_port="6380")


redis = create_redis_fixture(scope="function")
REDIS_ELEMENTS_COUNT = 123

QNAME_SUFFIX = "qsuffix"
PROCESS_TYPE = "process_type"


class TestBuffer:
    def test_when_name_suffix_is_not_supplied(self):
        assert Buffer.INBOX.inbox_name() == "inbox"
        assert Buffer.IN_FLIGHT.inbox_name() == "in-flight"

    def test_when_name_suffix_is_supplied(self):
        assert Buffer.INBOX.inbox_name("test") == "inbox:test"
        assert Buffer.IN_FLIGHT.inbox_name("test") == "in-flight:test"

    def test_when_name_suffix_process_is_supplied(self):
        assert Buffer.INBOX.inbox_name("test", "normal") == "inbox:test:normal"
        assert Buffer.IN_FLIGHT.inbox_name("test", "normal") == "in-flight:test:normal"

    def test_when_get_inflight_name_suffix_is_not_supplied(self):
        receipt = uuid4()
        assert Buffer.INBOX.inflight_name(receipt=receipt) == f"in-flight:{receipt}"
        assert Buffer.IN_FLIGHT.inflight_name(receipt=receipt) == f"in-flight:{receipt}"

    def test_when_get_inflight_name_suffix_is_supplied(self):
        receipt = uuid4()
        assert Buffer.INBOX.inflight_name(receipt=receipt, suffix="test") == f"in-flight:test:{receipt}"
        assert Buffer.IN_FLIGHT.inflight_name(receipt=receipt, suffix="test") == f"in-flight:test:{receipt}"

    def test_when_get_inflight_name_suffix_process_type_is_supplied(self):
        receipt = uuid4()
        assert (
            Buffer.INBOX.inflight_name(receipt=receipt, suffix="test", process_type="normal")
            == f"in-flight:test:normal:{receipt}"
        )
        assert (
            Buffer.IN_FLIGHT.inflight_name(receipt=receipt, suffix="test", process_type="normal")
            == f"in-flight:test:normal:{receipt}"
        )


class TestRedisQueue:
    @pytest.fixture(autouse=True)
    def app(self):
        config: Config = Test()
        config.REDIS_ENABLED = True
        app = Flask(config.NOTIFY_ENVIRONMENT)
        create_app(app, config)
        ctx = app.app_context()
        ctx.push()
        with app.test_request_context():
            yield app
        ctx.pop()
        return app

    @pytest.fixture()
    def redis_queue(self, app):
        q = RedisQueue(QNAME_SUFFIX, expire_inflight_after_seconds=1)
        q.init_app(flask_redis, metrics_logger)
        return q

    @pytest.fixture()
    def redis_queue_with_process(self, app):
        q = RedisQueue(QNAME_SUFFIX, expire_inflight_after_seconds=1, process_type=PROCESS_TYPE)
        q.init_app(flask_redis, metrics_logger)
        return q

    @contextmanager
    def given_inbox_with_one_element(self, redis, redis_queue):
        self.delete_all_list(redis)
        notification = generate_element()
        try:
            redis_queue.publish(notification)
            yield
        finally:
            self.delete_all_list(redis)

    @contextmanager
    def given_inbox_with_one_element_process_type(self, redis, redis_queue_with_process):
        self.delete_all_list(redis)
        notification = generate_element()
        try:
            redis_queue_with_process.publish(notification)
            yield
        finally:
            self.delete_all_list(redis)

    @contextmanager
    def given_inbox_with_many_indexes(self, redis, redis_queue):
        self.delete_all_list(redis)
        try:
            indexes = [str(i) for i in range(0, REDIS_ELEMENTS_COUNT)]
            [redis_queue.publish(index) for index in indexes]
            yield
        finally:
            self.delete_all_list(redis)

    @contextmanager
    def given_inbox_with_many_indexes_process_type(self, redis, redis_queue_with_process):
        self.delete_all_list(redis)
        try:
            indexes = [str(i) for i in range(0, REDIS_ELEMENTS_COUNT)]
            [redis_queue_with_process.publish(index) for index in indexes]
            yield
        finally:
            self.delete_all_list(redis)

    @pytest.mark.serial
    def delete_all_list(self, redis):
        self.delete_all_inbox(redis)
        self.delete_all_inflight(redis)

    @pytest.mark.serial
    def delete_all_inbox(self, redis):
        for key in redis.scan_iter(f"{Buffer.INBOX.value}*"):
            redis.delete(key)

    @pytest.mark.serial
    def delete_all_inflight(self, redis):
        for key in redis.scan_iter(f"{Buffer.IN_FLIGHT.value}*"):
            redis.delete(key)

    @pytest.mark.serial
    def test_put_messages(self, redis, redis_queue):
        element = generate_element()
        redis_queue.publish(element)
        assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == 1
        self.delete_all_list(redis)

    @pytest.mark.serial
    def test_put_messages_with_process(self, redis, redis_queue_with_process):
        element = generate_element()
        redis_queue_with_process.publish(element)
        assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX, process_type=PROCESS_TYPE)) == 1
        self.delete_all_list(redis)

    @pytest.mark.serial
    def test_polling_message(self, redis, redis_queue):
        with self.given_inbox_with_one_element(redis, redis_queue):
            (receipt, elements) = redis_queue.poll(10)
            assert len(elements) == 1
            assert isinstance(elements[0], str)
            assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == 0
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX)) == 1
            self.delete_all_list(redis)

    @pytest.mark.serial
    def test_polling_message_with_process(self, redis, redis_queue_with_process):
        with self.given_inbox_with_one_element_process_type(redis, redis_queue_with_process):
            (receipt, elements) = redis_queue_with_process.poll(10)
            assert len(elements) == 1
            assert isinstance(elements[0], str)
            assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX, process_type=PROCESS_TYPE)) == 0
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX, process_type=PROCESS_TYPE)) == 1
            self.delete_all_list(redis)

    @pytest.mark.serial
    @pytest.mark.parametrize("count", [0, 1, 98, 99, 100, 101, REDIS_ELEMENTS_COUNT, REDIS_ELEMENTS_COUNT + 1, 500])
    def test_polling_many_messages(self, redis, redis_queue, count):
        with self.given_inbox_with_many_indexes(redis, redis_queue):
            real_count = count if count < REDIS_ELEMENTS_COUNT else REDIS_ELEMENTS_COUNT
            (receipt, elements) = redis_queue.poll(count)
            assert len(elements) == real_count
            if count < REDIS_ELEMENTS_COUNT:
                assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) > 0
            else:
                assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == 0
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX)) == real_count
            self.delete_all_list(redis)

    @pytest.mark.serial
    @pytest.mark.parametrize("count", [0, 1, 98, 99, 100, 101, REDIS_ELEMENTS_COUNT, REDIS_ELEMENTS_COUNT + 1, 500])
    def test_polling_many_messages_with_process(self, redis, redis_queue_with_process, count):
        with self.given_inbox_with_many_indexes(redis, redis_queue_with_process):
            real_count = count if count < REDIS_ELEMENTS_COUNT else REDIS_ELEMENTS_COUNT
            (receipt, elements) = redis_queue_with_process.poll(count)
            assert len(elements) == real_count
            if count < REDIS_ELEMENTS_COUNT:
                assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX, process_type=PROCESS_TYPE)) > 0
            else:
                assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX, process_type=PROCESS_TYPE)) == 0
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX, process_type=PROCESS_TYPE)) == real_count
            self.delete_all_list(redis)

    @pytest.mark.serial
    @pytest.mark.parametrize("suffix", ["smss", "emails", "🎅", "", None])
    def test_polling_message_with_custom_inbox_name(self, redis, suffix):
        self.delete_all_list(redis)
        try:
            redis_queue = RedisQueue(suffix)
            redis_queue.init_app(flask_redis, metrics_logger)
            element = generate_element()
            redis_queue.publish(element)
            assert redis.llen(Buffer.INBOX.inbox_name(suffix)) == 1

            (receipt, elements) = redis_queue.poll(10)
            assert len(elements) == 1
            assert redis.llen(Buffer.INBOX.inbox_name(suffix)) == 0
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, suffix)) == 1

            redis_queue.acknowledge(receipt)
            assert redis.llen(Buffer.INBOX.inbox_name(suffix)) == 0
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, suffix)) == 0
        finally:
            self.delete_all_list(redis)

    @pytest.mark.serial
    def test_polling_with_empty_inbox(self, redis, redis_queue):
        self.delete_all_list(redis)
        (receipt, elements) = redis_queue.poll(10)
        assert len(elements) == 0
        assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == 0
        assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX)) == 0

    @pytest.mark.serial
    def test_polling_with_zero_count(self, redis, redis_queue):
        with self.given_inbox_with_one_element(redis, redis_queue):
            (receipt, elements) = redis_queue.poll(0)
            assert len(elements) == 0
            assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == 1
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX)) == 0

    @pytest.mark.serial
    def test_polling_with_negative_count(self, redis, redis_queue):
        with self.given_inbox_with_one_element(redis, redis_queue):
            (receipt, elements) = redis_queue.poll(-1)
            assert len(elements) == 0
            assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == 1
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX)) == 0

    @pytest.mark.serial
    def test_acknowledged_messages(self, redis, redis_queue):
        with self.given_inbox_with_one_element(redis, redis_queue):
            (receipt, elements) = redis_queue.poll(10)
            assert redis_queue.acknowledge(receipt)
            assert len(elements) > 0
            assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == 0
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX)) == 0
            assert len(redis.keys("*")) == 0

    @pytest.mark.serial
    def test_acknowledge_invalid_inflight(self, redis, redis_queue):
        with self.given_inbox_with_one_element(redis, redis_queue):
            (receipt, elements) = redis_queue.poll(10)
            assert not redis_queue.acknowledge("11111111-1111-1111-1111-1111")
            assert len(elements) > 0
            assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == 0
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX)) == 1
            assert len(redis.keys("*")) == 1

    @pytest.mark.serial
    def test_expire_inflights(self, redis, redis_queue):
        with self.given_inbox_with_many_indexes(redis, redis_queue):
            inbox_name = Buffer.INBOX.inbox_name(QNAME_SUFFIX)
            expected_inbox_contents = redis.lrange(inbox_name, 0, REDIS_ELEMENTS_COUNT)
            redis.set("not_inflight", "test")
            (receipt1, _) = redis_queue.poll(10)
            redis_queue.poll(10)
            redis_queue.poll(10)
            time.sleep(2)
            redis_queue.expire_inflights()

            assert redis.llen(inbox_name) == REDIS_ELEMENTS_COUNT
            actual_inbox_contents = redis.lrange(inbox_name, 0, REDIS_ELEMENTS_COUNT)
            assert sorted(expected_inbox_contents) == sorted(actual_inbox_contents)
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt1, QNAME_SUFFIX)) == 0
            assert redis.get("not_inflight") == b"test"

    @pytest.mark.serial
    def test_expire_inflights_does_not_expire_early(self, redis, redis_queue):
        with self.given_inbox_with_many_indexes(redis, redis_queue):
            (receipt, _) = redis_queue.poll(10)
            redis_queue.expire_inflights()
            assert redis.llen(Buffer.INBOX.inbox_name(QNAME_SUFFIX)) == REDIS_ELEMENTS_COUNT - 10
            assert redis.llen(Buffer.IN_FLIGHT.inflight_name(receipt, QNAME_SUFFIX)) == 10

    @pytest.mark.serial
    def test_messages_serialization_after_poll(self, redis, redis_queue):
        self.delete_all_list(redis)
        notification = (
            "{'id': '0ba0ff51-ec82-4835-b828-a24fec6124ab', 'type': 'email', 'email_address': 'success@simulator.amazonses.com'}"
        )
        redis_queue.publish(notification)
        (_, elements) = redis_queue.poll(1)

        assert len(elements) > 0
        assert type(elements) is list
        assert type(elements[0]) is str
        assert elements[0] == notification

        self.delete_all_list(redis)


@pytest.mark.usefixtures("notify_api")
class TestMockQueue:
    @pytest.fixture
    def mock_queue(self):
        return MockQueue()

    def test_polling_messages_from_queue(self, mock_queue):
        (receipt, elements) = mock_queue.poll(10)
        assert elements is not None
        assert len(elements) == 10

    def test_publish_mesages_on_queue(self, mock_queue):
        element = generate_element()
        mock_queue.publish(element)

        # This should not add change internal data structure
        # or differ from a random output generation due to the
        # nature of MockQueue.
        (_, elements) = mock_queue.poll(1)
        assert elements is not None
        assert len(elements) == 1
        assert element != elements[0]

    def test_acknowledged_messages(self, mock_queue):
        mock_queue.acknowledge([1, 2, 3])


class TestRedisQueueMetricUsage:
    @pytest.fixture(autouse=True)
    def app(self):
        config: Config = Test()
        config.REDIS_ENABLED = True
        app = Flask(config.NOTIFY_ENVIRONMENT)
        create_app(app, config)
        ctx = app.app_context()
        ctx.push()
        with app.test_request_context():
            yield app
        ctx.pop()
        return app

    @pytest.fixture()
    def redis_queue(self, app):
        q = RedisQueue(QNAME_SUFFIX, expire_inflight_after_seconds=1)
        q.init_app(flask_redis, metrics_logger)
        return q

    @contextmanager
    def given_inbox_with_one_element(self, redis, redis_queue):
        self.delete_all_list(redis)
        notification = generate_element()
        try:
            redis_queue.publish(notification)
            yield
        finally:
            self.delete_all_list(redis)

    @contextmanager
    def given_inbox_with_many_indexes(self, redis, redis_queue):
        self.delete_all_list(redis)
        try:
            indexes = [str(i) for i in range(0, REDIS_ELEMENTS_COUNT)]
            [redis_queue.publish(index) for index in indexes]
            yield
        finally:
            self.delete_all_list(redis)

    @pytest.mark.serial
    def delete_all_list(self, redis):
        self.delete_all_inbox(redis)
        self.delete_all_inflight(redis)

    @pytest.mark.serial
    def delete_all_inbox(self, redis):
        for key in redis.scan_iter(f"{Buffer.INBOX.value}*"):
            redis.delete(key)

    @pytest.mark.serial
    def delete_all_inflight(self, redis):
        for key in redis.scan_iter(f"{Buffer.IN_FLIGHT.value}*"):
            redis.delete(key)

    @pytest.mark.serial
    def test_put_batch_saving_metric(self, redis, redis_queue, mocker):
        pbsm_mock = mocker.patch("app.queue.put_batch_saving_metric")
        element = generate_element()
        redis_queue.publish(element)
        assert pbsm_mock.assert_called_with(mock.ANY, mock.ANY, 1) is None
        self.delete_all_list(redis)

    @pytest.mark.serial
    def test_polling_metric(self, redis, redis_queue, mocker):
        with self.given_inbox_with_one_element(redis, redis_queue):
            pbsim_mock = mocker.patch("app.queue.put_batch_saving_inflight_metric")
            redis_queue.poll(10)
            assert pbsim_mock.assert_called_with(mock.ANY, mock.ANY, 1) is None

    @pytest.mark.serial
    def test_polling_metric_no_results(self, redis, redis_queue, mocker):
        with self.given_inbox_with_one_element(redis, redis_queue):
            pbsim_mock = mocker.patch("app.queue.put_batch_saving_inflight_metric")
            redis_queue.poll(10)
            assert pbsim_mock.assert_called_with(mock.ANY, mock.ANY, 1) is None
            pbsim_mock.reset_mock()
            redis_queue.poll(10)
            assert not pbsim_mock.called, "put_batch_saving_inflight_metric was called and should not have been"

    @pytest.mark.serial
    def test_acknowledged_metric(self, redis, redis_queue, mocker):
        with self.given_inbox_with_one_element(redis, redis_queue):
            pbsip_mock = mocker.patch("app.queue.put_batch_saving_inflight_processed")
            (receipt, _) = redis_queue.poll(10)
            redis_queue.acknowledge(receipt)
            assert pbsip_mock.assert_called_with(mock.ANY, mock.ANY, 1) is None

    def test_put_batch_saving_expiry_metric(self, redis, redis_queue, mocker):
        with self.given_inbox_with_many_indexes(redis, redis_queue):
            pbsem_mock = mocker.patch("app.queue.put_batch_saving_expiry_metric")
            Buffer.INBOX.inbox_name(QNAME_SUFFIX)
            redis.set("not_inflight", "test")
            redis_queue.poll(10)
            redis_queue.poll(10)
            redis_queue.poll(10)
            time.sleep(2)
            redis_queue.expire_inflights()
            assert pbsem_mock.assert_called_with(mock.ANY, mock.ANY, 3) is None
