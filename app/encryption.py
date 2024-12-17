from typing import Any, List, NewType, Optional, TypedDict, cast

from flask_bcrypt import check_password_hash, generate_password_hash
from itsdangerous import URLSafeSerializer
from typing_extensions import NotRequired  # type: ignore

SignedNotification = NewType("SignedNotification", str)


class NotificationDictToSign(TypedDict):
    # todo: remove duplicate keys
    # todo: remove all NotRequired and decide if key should be there or not
    id: NotRequired[str]
    template: str  # actually template_id
    service_id: NotRequired[str]
    template_version: int
    to: str  # recipient
    reply_to_text: NotRequired[str]
    personalisation: Optional[dict]
    simulated: NotRequired[bool]
    api_key: str
    key_type: str  # should be ApiKeyType but I can't import that here
    client_reference: Optional[str]
    queue: Optional[str]
    sender_id: Optional[str]
    job: NotRequired[str]  # actually job_id
    row_number: Optional[Any]  # should this be int or str?


class CryptoSigner:
    def init_app(self, app: Any, secret_key: str | List[str], salt: str) -> None:
        """Initialise the CryptoSigner class.

        Args:
            app (Any): The Flask app.
            secret_key (str | List[str]): The secret key or list of secret keys to use for signing.
            salt (str): The salt to use for signing.
        """
        self.app = app
        self.secret_key = cast(List[str], [secret_key] if isinstance(secret_key, str) else secret_key)
        self.serializer = URLSafeSerializer(secret_key)
        self.salt = salt

    def sign(self, to_sign: str | NotificationDictToSign) -> str | bytes:
        """Sign a string or dict with the class secret key and salt.

        Args:
            to_sign (str | NotificationDictToSign): The string or dict to sign.

        Returns:
            str | bytes: The signed string or bytes.
        """
        return self.serializer.dumps(to_sign, salt=self.salt)

    def sign_with_all_keys(self, to_sign: str | NotificationDictToSign) -> List[str | bytes]:
        """Sign a string or dict with all the individual keys in the class secret key list, and the class salt.

        Args:
            to_sign (str | NotificationDictToSign): The string or dict to sign.

        Returns:
            List[str | bytes]: A list of signed values.
        """
        signed: list[str | bytes] = []
        for k in reversed(self.secret_key):  # reversed so that the default key is last
            signed.append(URLSafeSerializer(k).dumps(to_sign, salt=self.salt))
        return signed

    def verify(self, to_verify: str | bytes) -> Any:
        """Checks the signature of a signed value and returns the original value.

        Args:
            to_verify (str | bytes): The signed value to check

        Returns:
            Original value if signature is valid, raises BadSignature otherwise

        Raises:
            BadSignature: If the signature is invalid
        """
        return self.serializer.loads(to_verify, salt=self.salt)

    def verify_unsafe(self, to_verify: str | bytes) -> Any:
        """Ignore the signature and return the original value that has been signed.
        Since this ignores the signature it should be used with caution.

        Args:
            to_verify (str | bytes): The signed value to unsign

        Returns:
            Any: Original value that has been signed
        """
        return self.serializer.loads_unsafe(to_verify)[1]


def hashpw(password):
    return generate_password_hash(password.encode("UTF-8"), 10).decode("utf-8")


def check_hash(password, hashed_password):
    # If salt is invalid throws a 500 should add try/catch here
    return check_password_hash(hashed_password, password)
