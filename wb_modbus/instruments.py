import atexit
import ipaddress
import sys
import termios
import time
from contextlib import contextmanager

from mqttrpc import client as rpcclient
from wb_common.mqtt_client import DEFAULT_BROKER_URL, MQTTClient

from . import logger, minimalmodbus


class PyserialBackendInstrument(minimalmodbus.Instrument):
    """
    Building a request; parsing a response via minimalmodbus's internal tools;
    Communicating with device via pyserial

    _communicate is vanilla-minimalmodbus, except all foregoing_noise_cancelling cases
    """

    def __init__(self, *args, **kwargs):
        self.foregoing_noise_cancelling = kwargs.pop(
            "foregoing_noise_cancelling", False
        )  # Some early WB7s have hardware bug, causing additional zero byte on RX after write to port
        super(PyserialBackendInstrument, self).__init__(*args, **kwargs)

    def _get_possible_correct_response_beginnings(self, rtu_request):
        """
        We assume, that correct-response-beginning is [slaveid][fcode] or [slaveid][errcode]
        """
        slaveid, fcode = rtu_request[0], rtu_request[1]
        err_fcode = ord(fcode) | (
            1 << minimalmodbus._BITNUMBER_FUNCTIONCODE_ERRORINDICATION
        )  # error code is fcode with msb bit set
        err_fcode = minimalmodbus._num_to_onebyte_string(err_fcode)
        return [slaveid + fcode, slaveid + err_fcode]

    def _write_to_bus(self, request):  # pulled out minimalmodbus write func
        self.serial.write(request)

    def _read_from_bus(self, number_of_bytes_to_read, minimum_silent_period):
        """
        If there is foregoing noise, number_of_bytes_to_read = noise_bytes + part_of_response
        => reading remained part_of_response per-byte
        """
        answer = self.serial.read(number_of_bytes_to_read)
        if self.foregoing_noise_cancelling:
            time.sleep(minimum_silent_period)
            while self.serial.inWaiting():
                answer += self.serial.read(1)
                time.sleep(minimum_silent_period)
        return answer

    def _communicate(self, request, number_of_bytes_to_read):
        """minimalmodbus's original docstring:

        Talk to the slave via a serial port.

        Args:
            request (str): The raw request that is to be sent to the slave.
            number_of_bytes_to_read (int): number of bytes to read

        Returns:
            The raw data (string) returned from the slave.

        Raises:
            TypeError, ValueError, ModbusException,
            serial.SerialException (inherited from IOError)

        Note that the answer might have strange ASCII control signs, which
        makes it difficult to print it in the promt (messes up a bit).
        Use repr() to make the string printable (shows ASCII values for control signs.)

        Will block until reaching *number_of_bytes_to_read* or timeout.

        If the attribute :attr:`Instrument.debug` is :const:`True`, the communication
        details are printed.

        If the attribute :attr:`Instrument.close_port_after_each_call` is :const:`True` the
        serial port is closed after each call.

        Timing::

                            Request from master (Master is writing)
                            |
                            |                             Response from slave (Master is reading)
                            |                             |
            --------R-------W-----------------------------R-------W-----------------------------
                     |     |                               |
                     |     |<------- Roundtrip time ------>|
                     |     |
                  -->|-----|<----- Silent period

        The resolution for Python's time.time() is lower on Windows than on Linux.
        It is about 16 ms on Windows according to
        https://stackoverflow.com/questions/157359/accurate-timestamping-in-python-logging

        For Python3, the information sent to and from pySerial should be of the type bytes.
        This is taken care of automatically by MinimalModbus.

        """
        minimalmodbus._check_string(request, minlength=1, description="request")
        minimalmodbus._check_int(number_of_bytes_to_read)

        if self.foregoing_noise_cancelling:
            possible_response_beginnings = self._get_possible_correct_response_beginnings(request)

        self._print_debug(
            "Will write to instrument (expecting {} bytes back): {!r} ({})".format(
                number_of_bytes_to_read, request, minimalmodbus._hexlify(request)
            )
        )

        if not self.serial.is_open:
            self._print_debug("Opening port {}".format(self.serial.port))
            self.serial.open()

        if self.clear_buffers_before_each_transaction:
            self._print_debug("Clearing serial buffers for port {}".format(self.serial.port))
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()

        if sys.version_info[0] > 2:
            request = bytes(request, encoding="latin1")  # Convert types to make it Python3 compatible

        # Sleep to make sure 3.5 character times have passed
        minimum_silent_period = minimalmodbus._calculate_minimum_silent_period(self.serial.baudrate)
        time_since_read = minimalmodbus._now() - minimalmodbus._latest_read_times.get(self.serial.port, 0)

        if time_since_read < minimum_silent_period:
            sleep_time = minimum_silent_period - time_since_read

            if self.debug:
                template = (
                    "Sleeping {:.2f} ms before sending. "
                    + "Minimum silent period: {:.2f} ms, time since read: {:.2f} ms."
                )
                text = template.format(
                    sleep_time * minimalmodbus._SECONDS_TO_MILLISECONDS,
                    minimum_silent_period * minimalmodbus._SECONDS_TO_MILLISECONDS,
                    time_since_read * minimalmodbus._SECONDS_TO_MILLISECONDS,
                )
                self._print_debug(text)

            time.sleep(sleep_time)

        elif self.debug:
            template = (
                "No sleep required before write. "
                + "Time since previous read: {:.2f} ms, minimum silent period: {:.2f} ms."
            )
            text = template.format(
                time_since_read * minimalmodbus._SECONDS_TO_MILLISECONDS,
                minimum_silent_period * minimalmodbus._SECONDS_TO_MILLISECONDS,
            )
            self._print_debug(text)

        # Write request
        latest_write_time = minimalmodbus._now()
        self._write_to_bus(request)

        # Read and discard local echo
        if self.handle_local_echo:
            local_echo_to_discard = self.serial.read(len(request))
            if self.debug:
                template = "Discarding this local echo: {!r} ({} bytes)."
                text = template.format(local_echo_to_discard, len(local_echo_to_discard))
                self._print_debug(text)
            if local_echo_to_discard != request:
                template = (
                    "Local echo handling is enabled, but the local echo does "
                    + "not match the sent request. "
                    + "Request: {!r} ({} bytes), local echo: {!r} ({} bytes)."
                )
                text = template.format(
                    request,
                    len(request),
                    local_echo_to_discard,
                    len(local_echo_to_discard),
                )
                raise minimalmodbus.LocalEchoError(text)

        # Read response
        answer = self._read_from_bus(number_of_bytes_to_read, minimum_silent_period)
        minimalmodbus._latest_read_times[self.serial.port] = minimalmodbus._now()

        if self.close_port_after_each_call:
            self._print_debug("Closing port {}".format(self.serial.port))
            self.serial.close()

        if sys.version_info[0] > 2:
            # Convert types to make it Python3 compatible
            answer = str(answer, encoding="latin1")

        if self.foregoing_noise_cancelling:
            for bs in possible_response_beginnings:
                noise, sep, ret = answer.partition(bs)
                if ret:  # There is something after possible response beginning
                    self._print_debug(
                        "Foregoing noise cancelling:\n\tPlain response: {}\n\tNoise: {}; Answer: {}".format(
                            *map(lambda x: minimalmodbus._hexlify(x), (answer, noise, sep + ret))
                        )
                    )
                    answer = sep + ret
                    break

        if self.debug:
            template = (
                "Response from instrument: {!r} ({}) ({} bytes), "
                + "roundtrip time: {:.1f} ms. Timeout for reading: {:.1f} ms.\n"
            )
            text = template.format(
                answer,
                minimalmodbus._hexlify(answer),
                len(answer),
                (minimalmodbus._latest_read_times.get(self.serial.port, 0) - latest_write_time)
                * minimalmodbus._SECONDS_TO_MILLISECONDS,
                self.serial.timeout * minimalmodbus._SECONDS_TO_MILLISECONDS,
            )
            self._print_debug(text)

        if not answer:
            raise minimalmodbus.NoResponseError("No communication with the instrument (no answer)")

        return answer


class StopbitsTolerantInstrument(PyserialBackendInstrument):
    """
    Receiving with 1 stopbit no matter, what uart settings are.
    Setting stopbits to 1 between sending request and receiving response.
    """

    def _set_stopbits_onthefly(self, stopbits):
        """
        We need to ensure, all data has gone from buffers before setting stopbits to avoid payload corruption
        """
        termios.tcdrain(self.serial.fd)

        self.serial._stopbits = stopbits
        (iflag, oflag, cflag, lflag, ispeed, ospeed, cc) = termios.tcgetattr(self.serial.fd)
        if stopbits == 1:
            cflag &= ~termios.CSTOPB
        else:  # 2sb
            cflag |= termios.CSTOPB
        termios.tcsetattr(self.serial.fd, termios.TCSADRAIN, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])

        termios.tcdrain(self.serial.fd)

    def _write_to_bus(self, request):
        """
        Set stopbits-to-receive after all data-to-send goes out from output buffer
        """
        self._initial_stopbits = self.serial._stopbits
        super(StopbitsTolerantInstrument, self)._write_to_bus(request)
        write_ts = time.time()
        while (self.serial.out_waiting > 0) or (self.serial.in_waiting == 0):
            if time.time() - write_ts < self.serial.timeout:
                time.sleep(0.1)
            else:
                if (self.serial.out_waiting == 0) and (self.serial.in_waiting == 0):
                    raise minimalmodbus.NoResponseError("No communication with the instrument (no answer)")
                else:
                    raise minimalmodbus.MasterReportedException(
                        "Output serial buffer is not empty after %.2fs (serial.timeout)" % self.serial.timeout
                    )
        self._set_stopbits_onthefly(stopbits=1)

    def _read_from_bus(self, number_of_bytes_to_read, minimum_silent_period):
        """
        Initial stopbits (to-write) are setting just after data has received
        """
        ret = super(StopbitsTolerantInstrument, self)._read_from_bus(
            number_of_bytes_to_read, minimum_silent_period
        )
        self._set_stopbits_onthefly(self._initial_stopbits)
        return ret


class RPCError(minimalmodbus.MasterReportedException):
    pass


class RPCConnectionError(RPCError):
    pass


class RPCCommunicationError(RPCError):
    pass


class PySerialMock:
    """
    .bindings and .minimalmodbus assume pyserial-like obj under the hood
    """

    SERIAL_SETTINGS = {"baudrate": 9600, "parity": "N", "stopbits": 2}

    def __getattr__(self, name):
        def wrapper(*args, **kwargs):
            logger.debug(
                "Calling undefined %s(args: %s; kwargs: %s) on %s",
                name,
                str(args),
                str(kwargs),
                self.__class__.__name__,
            )
            return self

        setattr(self, name, wrapper)
        return wrapper

    def _reconfigure_port(self):
        pass

    def apply_settings(self, settings):
        self.SERIAL_SETTINGS.update(settings)


class SerialRPCBackendInstrument(minimalmodbus.Instrument):
    """
    Generic minimalmodbus instrument's logic with mqtt-rpc to wb-mqtt-serial as transport
    (instead of pyserial)
    """

    _MQTT_BROKER_URL = DEFAULT_BROKER_URL
    _MQTT_CONNECTIONS = {}

    RPC_ERR_STATES = {"JSON_PARSE": -32700, "REQUEST_HANDLING": -32000, "REQUEST_TIMEOUT": -32100}

    def __init__(self, port, slaveaddress, **kwargs):
        self.broker_url = kwargs.get("broker", self._MQTT_BROKER_URL)
        self.mqtt_client_name = "minimalmodbus-rpc-instrument"

        # required minimalmodbus's internals
        self.address = slaveaddress
        self.mode = kwargs.get("mode", minimalmodbus.MODE_RTU)
        self.precalculate_read_size = True
        self.debug = kwargs.get("debug", False)
        self.close_port_after_each_call = False
        self.port = port

        # respect current .minimalmodbus & .bindings design
        self.serial = PySerialMock()
        self.serial.port = port

    def __repr__(self):
        """Give string representation of the :class:`.Instrument` object."""
        template = (
            "{}.{}<id=0x{:x}, address={}, mode={}, " + "precalculate_read_size={}, " + "debug={}, serial={}>"
        )
        return template.format(
            self.__module__,
            self.__class__.__name__,
            id(self),
            self.address,
            self.mode,
            self.precalculate_read_size,
            self.debug,
            self.serial,
        )

    @property
    def mqtt_connections(self):
        return type(self)._MQTT_CONNECTIONS

    def close_mqtt(self, broker_url):
        client = self.mqtt_connections.get(broker_url)

        if client:
            client.stop()
            self.mqtt_connections.pop(broker_url)
            logger.debug("Mqtt: close %s", broker_url)
        else:
            logger.warning("Mqtt connection %s not found in active ones!", broker_url)

    @contextmanager
    def get_mqtt_client(self, broker_url):
        client = self.mqtt_connections.get(broker_url)

        if client:
            yield client
        else:
            try:
                client = MQTTClient(self.mqtt_client_name, broker_url)
                logger.debug("New mqtt connection: %s", broker_url)
                client.start()
                self.mqtt_connections.update({broker_url: client})
                yield client
            except (rpcclient.TimeoutError, OSError) as e:
                raise RPCConnectionError from e
            finally:
                atexit.register(lambda: self.close_mqtt(broker_url))

    def get_transport_params(self):
        return {
            "path": self.serial.port,
            "baud_rate": self.serial.SERIAL_SETTINGS["baudrate"],
            "parity": self.serial.SERIAL_SETTINGS["parity"],
            "data_bits": 8,
            "stop_bits": self.serial.SERIAL_SETTINGS["stopbits"],
        }

    def _communicate(self, request, number_of_bytes_to_read):
        minimalmodbus._check_string(request, minlength=1, description="request")
        minimalmodbus._check_int(number_of_bytes_to_read)

        min_response_timeout = 0.5  # hardcoded in wb-mqtt-serial's validation

        rpc_request = {
            "response_size": number_of_bytes_to_read,
            "format": "HEX",
            "msg": minimalmodbus._hexencode(request),
            "response_timeout": round(max(self.serial.timeout, min_response_timeout) * 1e3),
        }
        rpc_request.update(self.get_transport_params())

        with self.get_mqtt_client(self.broker_url) as mqtt_client:
            rpc_call_timeout = 10
            try:
                rpc_client = rpcclient.TMQTTRPCClient(mqtt_client)
                mqtt_client.on_message = rpc_client.on_mqtt_message
                logger.debug("RPC Client -> %s (rpc timeout: %ds)", rpc_request, rpc_call_timeout)
                response = rpc_client.call("wb-mqtt-serial", "port", "Load", rpc_request, rpc_call_timeout)
                logger.debug("RPC Client <- %s", response)
            except rpcclient.MQTTRPCError as e:
                reraise_err = (
                    minimalmodbus.NoResponseError
                    if e.code == self.RPC_ERR_STATES["REQUEST_HANDLING"]
                    else RPCCommunicationError
                )
                raise reraise_err from e
            else:
                return minimalmodbus._hexdecode(str(response.get("response", "")))


class TCPRPCBackendInstrument(SerialRPCBackendInstrument):
    def __init__(self, ip_addr_port, slaveaddress, **kwargs):
        ip, _, port = ip_addr_port.partition(":")
        try:
            self.ip = ipaddress.ip_address(ip).exploded
            self.tcp_port = int(port)
        except ValueError as e:
            raise RPCConnectionError('Format should be "valid_ip_addr:port"') from e

        super().__init__(port=None, slaveaddress=slaveaddress, **kwargs)

    def get_transport_params(self):
        return {
            "ip": self.ip,
            "port": self.tcp_port,
        }
