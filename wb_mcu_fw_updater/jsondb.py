import json
import os

from . import CONFIG, logger


class FixedLengthList(list):
    """
    Removing elements from beginning, when <MAXLEN> has achieved.

    A JsonDB's default inner container.
    """

    MAXLEN = CONFIG["MAX_DB_RECORDS"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._keep_length()

    def _keep_length(self):
        while len(self) > self.MAXLEN:
            self.pop(0)

    def append(self, item):
        super().append(item)
        self._keep_length()

    def extend(self, sequence):
        super().extend(sequence)
        self._keep_length()

    def insert(self, index, element):
        super().insert(index, element)
        self._keep_length()


class JsonDB:
    """
    Storing information about device's fw_signature.
    """

    _SLAVEID = "slaveid"
    _PORT = "port"
    _FW_SIGNATURE = "fw_signature"

    def __init__(self, db_fname):
        self.db_fname = os.path.expanduser(db_fname)
        self.load(self.db_fname)

    def load(self, db_fname):
        if os.path.exists(db_fname):
            logger.debug("Loading db from file: %s", db_fname)
            with open(db_fname, "r", encoding="utf-8") as file:
                self.container = FixedLengthList(json.load(file))
        else:
            logger.debug("File %s not found! Initiallizing empty db", db_fname)
            self.container = FixedLengthList()

    def dump(self):
        try:
            with open(self.db_fname, "w+", encoding="utf-8") as file:
                json.dump(self.container, file)
            logger.debug("Has saved db to %s", self.db_fname)
        except PermissionError:
            logger.error("Haven't rights to write %s! Try with sudo", self.db_fname, exc_info=True)

    def _find(self, slaveid, port, sequence):
        for index, device in enumerate(sequence):
            if (device[self._SLAVEID] == slaveid) and (device[self._PORT] == port):
                return index
        return None

    def save(self, slaveid, port, fw_signature):
        existing_device_index = self._find(slaveid, port, sequence=self.container)
        if existing_device_index is not None:  # Could be zero
            removed_device = self.container.pop(existing_device_index)
            logger.debug("Removing device: %s", str(removed_device))
        device = {self._SLAVEID: slaveid, self._PORT: port, self._FW_SIGNATURE: fw_signature}
        self.container.append(device)

    def get_fw_signature(self, slaveid, port):
        """
        Searching in a reversed shadow-copy of container.
        Lost devices are assumed to be neer the end.
        """
        sequence = self.container[::-1]
        found_index = self._find(slaveid, port, sequence)
        return sequence[found_index][self._FW_SIGNATURE] if found_index is not None else None
