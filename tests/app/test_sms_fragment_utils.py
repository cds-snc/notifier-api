import pytest
from notifications_utils.clients.redis import sms_daily_count_cache_key

from app.dao.services_dao import fetch_todays_total_sms_count
from tests.conftest import set_config


@pytest.mark.parametrize("redis_value,db_value,expected_result", [(None, 5, 5), ("3", 5, 3)])
def test_fetch_todays_total_sms_count(client, mocker, sample_service, redis_value, db_value, expected_result):
    cache_key = sms_daily_count_cache_key(sample_service.id)
    mocker.patch("app.redis_store.get", lambda x: redis_value if x == cache_key else None)
    mocked_set = mocker.patch("app.redis_store.set")
    mocker.patch("app.sms_fragment_utils.fetch_todays_total_sms_count", return_value=db_value)
    mocker.patch("app.dao.users_dao.user_can_be_archived", return_value=False)

    with set_config(client.application, "REDIS_ENABLED", True):
        actual_result = fetch_todays_total_sms_count(sample_service.id)

        assert actual_result == expected_result
        if redis_value is None:
            assert mocked_set.called_once_with(
                cache_key,
                db_value,
            )
        else:
            mocked_set.assert_not_called()
