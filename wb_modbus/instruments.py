import sys
import time
from . import minimalmodbus


class PyserialBackendInstrument(minimalmodbus.Instrument):
    """
    Building a request; parsing a response via minimalmodbus's internal tools;
    Communicating with device via pyserial

    _communicate is vanilla-minimalmodbus, except all foregoing_noise_cancelling cases
    """

    def __init__(self, *args, **kwargs):
        self.foregoing_noise_cancelling = kwargs.pop('foregoing_noise_cancelling', False)  # Some early WB7s have hardware bug, causing additional zero byte on RX after write to port
        super(PyserialBackendInstrument, self).__init__(*args, **kwargs)

    def _get_possible_correct_response_beginnings(self, rtu_request):
        """
        We assume, that correct-response-beginning is [slaveid][fcode] or [slaveid][errcode]
        """
        slaveid, fcode = rtu_request[0], rtu_request[1]
        err_fcode = ord(fcode) | (1 << minimalmodbus._BITNUMBER_FUNCTIONCODE_ERRORINDICATION)  # error code is fcode with msb bit set
        err_fcode = minimalmodbus._num_to_onebyte_string(err_fcode)
        return [slaveid + fcode, slaveid + err_fcode]

    def _communicate(self, request, number_of_bytes_to_read):
        """ minimalmodbus's original docstring:

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
            self._print_debug(
                "Clearing serial buffers for port {}".format(self.serial.port)
            )
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()

        if sys.version_info[0] > 2:
            request = bytes(
                request, encoding="latin1"
            )  # Convert types to make it Python3 compatible

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
        self.serial.write(request)

        # Read and discard local echo
        if self.handle_local_echo:
            local_echo_to_discard = self.serial.read(len(request))
            if self.debug:
                template = "Discarding this local echo: {!r} ({} bytes)."
                text = template.format(
                    local_echo_to_discard, len(local_echo_to_discard)
                )
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
        answer = self.serial.read(number_of_bytes_to_read)
        if self.foregoing_noise_cancelling:
            time.sleep(minimum_silent_period)
            while self.serial.inWaiting():
                answer += self.serial.read(1)
                time.sleep(minimum_silent_period)
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
                    self._print_debug("Foregoing noise cancelling:\n\tPlain response: {}\n\tNoise: {}; Answer: {}".format(*map(lambda x: minimalmodbus._hexlify(x), (answer, noise, sep + ret))))
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