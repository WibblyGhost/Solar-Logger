# pylint: disable=missing-function-docstring, missing-module-docstring, no-self-use, redefined-outer-name, protected-access
import logging
from unittest import mock

from paho.mqtt.client import Client, MQTTMessage
from pymate.value import Value
from pytest import LogCaptureFixture, fixture, mark, raises
from pytest_mock import MockerFixture, mocker
from src.classes.common_classes import QueuePackage

from src.classes.mqtt_classes import MqttConnector, MqttTopics, PyMateDecoder
from src.helpers.consts import THREADED_QUEUE
from tests.config.consts import (
    FAKE,
    TEST_MAX_QUEUE_LENGTH,
    TestDC,
    TestFX,
    TestMqttTopics,
    TestMX,
    TestSecretStore,
)


class TestError(Exception):
    """Custom exception for use in tests"""


def dict_to_str(dictionary: dict):
    result = {}
    for key, value in dictionary.items():
        result[key] = str(value)
    return result


class TestPyMateDecoder:
    """Test class for PyMate Decoder"""

    def test_passes_detach_time(self):
        pymate_decoder = PyMateDecoder()
        result = pymate_decoder.detach_time(msg=TestFX.bytearray, padding_at_end=2)

        assert result == (67108864, b"t\x00\x04\x00\x02\x01\x12")

    def test_passes_dc_decoder(self):
        pymate_decoder = PyMateDecoder()
        decoded_result = pymate_decoder.dc_decoder(TestDC.bytearray)
        str_decoded_result = dict_to_str(decoded_result)
        str_dc_array = dict_to_str(TestDC.array)

        assert isinstance(decoded_result["bat_current"], Value)
        assert str_decoded_result == str_dc_array

    def test_passes_fx_decoder(self):
        pymate_decoder = PyMateDecoder()
        decoded_result = pymate_decoder.fx_decoder(TestFX.bytearray)
        str_decoded_result = dict_to_str(decoded_result)
        str_fx_array = dict_to_str(TestFX.array)

        assert isinstance(decoded_result["output_voltage"], Value)
        assert str_decoded_result == str_fx_array

    def test_passes_mx_decoder(self):
        pymate_decoder = PyMateDecoder()
        decoded_result = pymate_decoder.mx_decoder(TestMX.bytearray)
        str_decoded_result = dict_to_str(decoded_result)
        str_mx_array = dict_to_str(TestMX.array)

        assert isinstance(decoded_result["amp_hours"], Value)
        assert str_decoded_result == str_mx_array


def test_mqtt_topics_consistent():
    def get_custom_attributes(cls):
        return {func: getattr(cls, func) for func in dir(cls) if func[0] != "_"}

    defined_vars = get_custom_attributes(MqttTopics)
    test_dict = get_custom_attributes(TestMqttTopics)
    assert defined_vars == test_dict


@fixture
def mqtt_fixture():
    mqtt_connector = MqttConnector(secret_store=TestSecretStore)
    return mqtt_connector


class TestMqttConnector:
    """Test class for MQTT Connector"""

    def test_logs_on_socket_open(
        self, mqtt_fixture: MqttConnector, caplog: LogCaptureFixture
    ):
        caplog.set_level(logging.DEBUG)
        userdata = FAKE.pystr()
        sock = FAKE.pystr()

        mqtt_fixture._on_socket_open(_client=FAKE.pystr(), userdata=userdata, sock=sock)

        assert f"Socket open debug args, {userdata}, {sock}" in caplog.text

    def test_logs_on_socket_close(
        self, mqtt_fixture: MqttConnector, caplog: LogCaptureFixture
    ):
        caplog.set_level(logging.DEBUG)
        userdata = FAKE.pystr()
        sock = FAKE.pystr()

        mqtt_fixture._on_socket_close(
            _client=FAKE.pystr(), userdata=userdata, sock=sock
        )

        assert f"Socket close debug args, {userdata}, {sock}" in caplog.text

    def test_logs_on_subscribe(
        self, mqtt_fixture: MqttConnector, caplog: LogCaptureFixture
    ):
        caplog.set_level(logging.DEBUG)
        granted_qos = FAKE.pyint()
        mid = FAKE.pystr()
        userdata = FAKE.pystr()

        mqtt_fixture._on_subscribe(
            _client=FAKE.pystr(), userdata=userdata, mid=mid, granted_qos=granted_qos
        )

        assert "Subscribed to MQTT topic" in caplog.text
        assert f"MQTT topic returns QoS level of {granted_qos}" in caplog.text
        assert f"Subscribe debug args, {userdata}, {mid}, {granted_qos}" in caplog.text

    def test_logs_on_unsubscribe(
        self, mqtt_fixture: MqttConnector, caplog: LogCaptureFixture
    ):
        caplog.set_level(logging.DEBUG)
        mid = FAKE.pystr()
        userdata = FAKE.pystr()

        mqtt_fixture._on_unsubscribe(_client=FAKE.pystr(), userdata=userdata, mid=mid)

        assert "Unsubscribed from MQTT topic" in caplog.text
        assert f"Unsubscribe debug args, {userdata}, {mid}" in caplog.text

    def test_on_connect_calls_subscribe(
        self,
        mocker: MockerFixture,
        mqtt_fixture: MqttConnector,
        caplog: LogCaptureFixture,
    ):
        caplog.set_level(logging.INFO)
        subscribe = mocker.patch("src.classes.mqtt_classes.Client.subscribe")
        userdata = FAKE.pystr()
        flags = FAKE.pystr()
        return_code = 0

        mqtt_fixture._on_connect(
            _client=FAKE.pystr(),
            userdata=userdata,
            flags=flags,
            return_code=return_code,
        )

        subscribe.assert_called_once_with(
            topic=f"{TestSecretStore.mqtt_secrets['mqtt_topic']}"
        )
        assert "Connecting to MQTT broker" in caplog.text

    @mark.parametrize("return_code", [1, 2, 3, 4, 5])
    def test_on_connect_fails_with_bad_return_code(
        self,
        mqtt_fixture: MqttConnector,
        return_code: int,
        caplog: LogCaptureFixture,
    ):
        caplog.set_level(logging.DEBUG)
        userdata = FAKE.pystr()
        flags = FAKE.pystr()
        return_codes = {
            0: "Connection successful",
            1: "Connection refused - incorrect protocol version",
            2: "Connection refused - invalid client identifier",
            3: "Connection refused - server unavailable",
            4: "Connection refused - bad username or password",
            5: "Connection refused - not authorized",
        }

        mqtt_fixture._on_connect(
            _client=FAKE.pystr(),
            userdata=userdata,
            flags=flags,
            return_code=return_code,
        )

        assert (
            f"Couldn't connect to MQTT broker returned code: {return_code}\n"
            f"{return_codes[return_code]}"
        ) in caplog.text
        assert f"Connect debug args, {userdata}, {flags}, {return_code}" in caplog.text

    def test_logs_on_disconnect(
        self, mqtt_fixture: MqttConnector, caplog: LogCaptureFixture
    ):
        caplog.set_level(logging.DEBUG)
        userdata = FAKE.pystr()
        return_code = FAKE.pyint()

        mqtt_fixture._on_disconnect(
            _client=FAKE.pystr(), userdata=userdata, return_code=return_code
        )

        assert "Disconnected from MQTT broker" in caplog.text
        assert f"Disconnect debug args, {userdata}, {return_code}" in caplog.text

    @mark.parametrize("status", ["online", "offline",])
    @mark.parametrize(
        "topic", [
            TestMqttTopics.mate_status,
            TestMqttTopics.dc_status,
            TestMqttTopics.fx_status,
            TestMqttTopics.mx_status,
        ]
    )
    def test_check_status_goes_offline(
        self,
        mocker: MockerFixture,
        mqtt_fixture: MqttConnector,
        topic: str,
        status: str,
        caplog: LogCaptureFixture,
    ):
        caplog.set_level(logging.INFO)
        # Force everything online and test the change to offline
        if status == "online":
            current_service_status = "offline"
        else:
            current_service_status = "online"
        status_values = [
            TestMqttTopics.mate_status,
            TestMqttTopics.dc_status,
            TestMqttTopics.fx_status,
            TestMqttTopics.mx_status,
        ]
        for value in status_values:
            mqtt_fixture._status[value] = current_service_status
        mqtt_message = mocker.MagicMock(MQTTMessage)
        mqtt_message.topic = topic
        mqtt_message.payload = bytes(status, "ascii")

        mqtt_fixture._check_status(msg=mqtt_message)

        if status == "online":
            assert f"{topic} is now {status}" in caplog.text
        else:
            assert f"{topic} has gone {status}" in caplog.text
        assert mqtt_fixture._status[topic] == status

    def test_passes_load_queue(
        self, mqtt_fixture: MqttConnector, caplog: LogCaptureFixture
    ):
        caplog.set_level(logging.INFO)
        measurement = FAKE.pystr()
        time_field = FAKE.date()
        payload_key = FAKE.pystr()
        payload_value = FAKE.pyfloat()

        mqtt_fixture._load_queue(
            measurement=measurement,
            time_field=time_field,
            payload={
                payload_key: str(payload_value),
            },
        )

        assert QueuePackage(
            measurement=measurement,
            time_field=time_field,
            field={payload_key: float(payload_value)},
        ) == THREADED_QUEUE.get(timeout=5)
        assert "Pushed items onto queue, queue now has 1 items" in caplog.text

    def test_waits_when_exceeding_max_queue_load(
        self,
        mocker: MockerFixture,
        mqtt_fixture: MqttConnector,
        caplog: LogCaptureFixture,
    ):
        caplog.set_level(logging.INFO)
        measurement = FAKE.pystr()
        time_field = FAKE.date()
        payload_key = FAKE.pystr()
        payload_value = FAKE.pyfloat()

        test_queue = []
        mocker.patch("src.classes.mqtt_classes.time.sleep", side_effect=TestError)
        with raises(TestError):
            for _ in range(0, TEST_MAX_QUEUE_LENGTH + 1):
                mqtt_fixture._load_queue(
                    measurement=measurement,
                    time_field=time_field,
                    payload={
                        payload_key: str(payload_value),
                    },
                )
                test_queue.append(
                    QueuePackage(
                        measurement=measurement,
                        time_field=time_field,
                        field={payload_key: float(payload_value)},
                    )
                )

        result_queue = []
        while not THREADED_QUEUE.empty():
            result_queue.append(THREADED_QUEUE.get(timeout=1))
        zipped_queues = zip(test_queue, result_queue)

        for test_item, result_item in zipped_queues:
            assert test_item == result_item
        assert (
            f"Pushed items onto queue, queue now has {TEST_MAX_QUEUE_LENGTH - 1} items"
            in caplog.text
        )

    @mark.skip(reason="test_passes_decode_message_dc needs to be implemented")
    def test_passes_decode_message_dc(self):
        raise NotImplementedError

    @mark.skip(reason="test_passes_decode_message_fx needs to be implemented")
    def test_passes_decode_message_fx(self):
        raise NotImplementedError

    @mark.skip(reason="test_passes_decode_message_mx needs to be implemented")
    def test_passes_decode_message_mx(self):
        raise NotImplementedError

    @mark.skip(reason="test_passes_on_message needs to be implemented")
    def test_passes_on_message(self):
        raise NotImplementedError

    @mark.skip(
        reason="test_on_message_doesnt_raise_on_exception needs to be implemented"
    )
    def test_on_message_doesnt_raise_on_exception(self):
        raise NotImplementedError

    def test_passes_get_mqtt_client(self):
        mqtt_connector = MqttConnector(TestSecretStore)

        with mock.patch("src.classes.mqtt_classes.Client.connect"):
            result = mqtt_connector.get_mqtt_client()

        assert isinstance(result, Client)
