import uuid

from app.models.models import InboundNumber


class TestGetInboundNumbers:

    def test_returns_empty_list_when_no_inbound_numbers(self, admin_request, mocker):
        mocker.patch('app.inbound_number.rest.dao_get_inbound_numbers', return_value=[])

        result = admin_request.get('inbound_number.get_inbound_numbers')

        assert result['data'] == []

    def test_returns_inbound_numbers(self, admin_request, mocker):
        inbound_number = InboundNumber()
        mocker.patch('app.inbound_number.rest.dao_get_inbound_numbers', return_value=[inbound_number])

        result = admin_request.get('inbound_number.get_inbound_numbers')

        assert result['data'] == [inbound_number.serialize()]


class TestGetInboundNumbersForService:

    def test_gets_empty_list(self, admin_request, mocker):
        dao_get_inbound_numbers_for_service = mocker.patch(
            'app.inbound_number.rest.dao_get_inbound_numbers_for_service',
            return_value=[]
        )

        service_id = uuid.uuid4()
        result = admin_request.get('inbound_number.get_inbound_numbers_for_service', service_id=service_id)

        assert result['data'] == []
        dao_get_inbound_numbers_for_service.assert_called_with(service_id)

    def test_gets_inbound_numbers(self, admin_request, mocker):
        inbound_number = InboundNumber()
        mocker.patch(
            'app.inbound_number.rest.dao_get_inbound_numbers_for_service',
            return_value=[inbound_number]
        )

        result = admin_request.get(
            'inbound_number.get_inbound_numbers_for_service',
            service_id=uuid.uuid4()
        )

        assert result['data'] == [inbound_number.serialize()]


class TestSetInboundNumberOff:

    def test_sets_inbound_number_active_flag_off(self, admin_request, mocker):
        dao_set_inbound_number_active_flag = mocker.patch('app.inbound_number.rest.dao_set_inbound_number_active_flag')

        inbound_number_id = uuid.uuid4()
        admin_request.post(
            'inbound_number.post_set_inbound_number_off',
            _expected_status=204,
            inbound_number_id=inbound_number_id
        )
        dao_set_inbound_number_active_flag.assert_called_with(inbound_number_id, active=False)


def test_get_available_inbound_numbers_returns_empty_list(admin_request):
    result = admin_request.get('inbound_number.get_available_inbound_numbers')

    assert result['data'] == []


def test_get_available_inbound_numbers(admin_request, sample_inbound_numbers):
    result = admin_request.get('inbound_number.get_available_inbound_numbers')

    assert len(result['data']) == 1
    assert result['data'] == [i.serialize() for i in sample_inbound_numbers if
                              i.service_id is None]


class TestCreateInboundNumber:

    def test_rejects_request_with_missing_data(self, admin_request):
        admin_request.post(
            'inbound_number.create_inbound_number',
            _data={},
            _expected_status=400
        )

    def test_rejects_request_with_unexpected_data(self, admin_request):
        admin_request.post(
            'inbound_number.create_inbound_number',
            _data={
                'number': 'some-number',
                'provider': 'some-provider',
                'service_id': 'some-service-id',
                'some_attribute_that_does_not_exist': 'blah'
            },
            _expected_status=400
        )

    def test_creates_inbound_number(self, admin_request, mocker):
        dao_create_inbound_number = mocker.patch('app.inbound_number.rest.dao_create_inbound_number')

        admin_request.post(
            'inbound_number.create_inbound_number',
            _data={
                'number': 'some-number',
                'provider': 'some-provider',
                'service_id': 'some-service-id'
            },
            _expected_status=201
        )

        args, _ = dao_create_inbound_number.call_args
        (created_inbound_number,) = args
        assert created_inbound_number.number == 'some-number'
        assert created_inbound_number.provider == 'some-provider'
        assert created_inbound_number.service_id == 'some-service-id'


class TestUpdateInboundNumber:

    def test_rejects_invalid_request(self, admin_request):
        admin_request.post(
            'inbound_number.update_inbound_number',
            _data={
                'some_attribute_that_does_not_exist': 'blah'
            },
            _expected_status=400,
            inbound_number_id=uuid.uuid4()
        )

    def test_updates_inbound_number(self, admin_request, mocker):
        inbound_number_id = uuid.uuid4()

        updated_inbound_number = InboundNumber()

        dao_update_inbound_number = mocker.patch(
            'app.inbound_number.rest.dao_update_inbound_number',
            return_value=updated_inbound_number
        )

        update_dictionary = {
            'number': 'some-number',
            'provider': 'some-provider',
            'service_id': 'some-service-id'
        }
        response = admin_request.post(
            'inbound_number.update_inbound_number',
            _data=update_dictionary,
            _expected_status=200,
            inbound_number_id=inbound_number_id
        )

        dao_update_inbound_number.assert_called_with(inbound_number_id, **update_dictionary)

        assert response['data'] == updated_inbound_number.serialize()
