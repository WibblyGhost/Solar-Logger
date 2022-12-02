"""
Classes file, contains methods for the Backup Reader which will read
and decode 'xz' compresses backup files and upload the decoded rows
to a INfluxDB server
"""
import logging
import lzma
import re

# import shlex
from datetime import datetime, timedelta

from dateutil import tz

from src.classes.mqtt_classes import MqttConnector, MqttTopics, PyMateDecoder

LOG_INTERVAL_QUEUE = 50
LOG_INTERVAL_TABLE = 100000


class BackupReader:
    """
    Class which handles reading 'xz' compressed backup files and uploading
    the decoded rows into an InfluxDB Server
    """

    def __init__(self):
        """
        Initialization for the Backup Reader
        """
        self.g_col_raw = None
        self.g_col_timestamp = None
        self.g_col_tzoffset = None
        self.g_lines_read = 0
        self.g_read_rows = False
        self.g_table_name = None

    @staticmethod
    def _start_table(table_name: str):
        """
        Indicates the start of table data
        :param table_name: name of the table
        """
        logging.info(f"Start of table {table_name}")

    @staticmethod
    def _column_in(
        table_name: str, row_num: int, timestamp: datetime, raw_packet: bytes
    ):
        """
        Called for each row of table data
        :param table_name: name of the table
        :param row_num: row number
        :param timestamp: datetime with timezone encoded
        :param raw_packet: bytes() to be passed to pymate decoder
        """
        # TODO: Look into disabling other loggers when this functions runs
        # Create unique handler names so we can later disable them
        # This is due to load_queue and offloading queues logging
        if table_name == "dc_status":  # 4,337,643 rows
            # NOTE: Don't log here since it severely slows down the reading
            dc_payload = PyMateDecoder.dc_decoder(raw_packet)
            MqttConnector.load_queue(
                measurement=MqttTopics.dc_name,
                time_field=timestamp,
                payload=dc_payload,
                log=(row_num % LOG_INTERVAL_QUEUE == 0),
            )

        elif table_name == "mx_status":  # 9,224,620 rows
            # NOTE: Don't log here since it severely slows down the reading
            mx_payload = PyMateDecoder.mx_decoder(raw_packet)
            MqttConnector.load_queue(
                measurement=MqttTopics.dc_name,
                time_field=timestamp,
                payload=mx_payload,
                log=(row_num % LOG_INTERVAL_QUEUE == 0),
            )

        elif table_name == "mx_logpage":  # 1762 rows
            pass

        elif table_name == "fx_status":  # 664,359 rows
            # NOTE: Don't log here since it severely slows down the reading
            fx_payload = PyMateDecoder.fx_decoder(raw_packet)
            MqttConnector.load_queue(
                measurement=MqttTopics.dc_name,
                time_field=timestamp,
                payload=fx_payload,
                log=(row_num % LOG_INTERVAL_QUEUE == 0),
            )

    @staticmethod
    def _end_table(table_name: str):
        """
        Indicates end of table data
        :param table_name: name of the table
        """
        logging.info(f"End of table {table_name}")

    def _extract_column_in(
        self,
        line,
    ):
        self.g_lines_read += 1

        cells = line.split("\t")
        timestamp = cells[self.g_col_timestamp]
        tzoffset = cells[self.g_col_tzoffset]
        raw_packet = cells[self.g_col_raw]

        # The timestamp is in UTC timezone...
        try:
            timestamp_utc = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            # Sometimes a timestamp will be missing the .microseconds part,
            # I think my script must have changed the encoding at some point...
            timestamp_utc = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")

        # Add the timezone info to the datetime.
        # In our case, this will either be 43200/UTC+12(NZST) or 46800/UTC+13(NZDT)
        tzinfo = tz.tzoffset("", timedelta(seconds=int(tzoffset)))
        timestamp = timestamp_utc.replace(tzinfo=tzinfo)

        # Decode the encoded packet data
        # Format is "\\x00112233"
        raw_packet = bytearray.fromhex(raw_packet[3:])

        # logging.info() will slow things down if you do it for every line,
        # so be selective about what you print here...
        if self.g_lines_read % LOG_INTERVAL_TABLE == 0:
            logging.info(f"Read {self.g_lines_read} lines \t{timestamp}")

        self._column_in(
            table_name=self.g_table_name,
            row_num=self.g_lines_read,
            timestamp=timestamp,
            raw_packet=raw_packet,
        )

    def _extract_start_table(self, matched_line: str) -> bool:
        self.g_table_name = matched_line[1]

        # g_columns = [c.replace(',','') for c in shlex.split(m[2])]
        # Split columns by comma, and remove quotes (used when a column name is a SQL keyword)
        g_columns = [c.replace('"', "").strip() for c in matched_line[2].split(",")]

        # Create a mapping of column name to column index
        g_columns_map = {k: i for i, k in enumerate(g_columns)}

        logging.info(f'Begin table "{self.g_table_name}"')
        logging.info(f'Columns: {",".join(g_columns)}')

        # Cache these indexes for performance...
        try:
            self.g_col_timestamp = g_columns_map["timestamp"]
            self.g_col_tzoffset = g_columns_map["tzoffset"]
            self.g_col_raw = g_columns_map["raw_packet"]
        except Exception as err:
            logging.info(f"Skipping table - missing one or more required columns {err}")
            raise

        # Data will follow, start reading lines on next loop iteration
        self.g_read_rows = True
        self.g_lines_read = 0

    def read_compressed_xz(self, file_path: str):
        """
        Reads, decodes and extracts a backed up database in the
        'xz' compressed format.
        :param file_path: path of the compressed 'xz' backup
        """
        # The backup is just a text file containing SQL commands compressed
        # with LZMA compression. The lzma module allows you to open it and
        # stream the file without decompressing the whole thing...
        # Which is just as well, as the full text size is about 3GB!
        with lzma.open(file_path, "r") as open_file:
            for line_raw in open_file:
                # Lines will be bytestrings, but we know the text file is ASCII-encoded
                line = line_raw.decode("ascii").strip()

                # Start reading in rows for the table until we hit an empty line,
                # if we have detected the start of the data.
                # This comes before the next if condition for efficiency, so we
                # don't have to run .startswith() for every line!
                if self.g_read_rows:
                    # logging.info('\r'+ln[:5])
                    if line == "\\." or not line:
                        self._end_table(self.g_table_name)
                        logging.info(f"Read {self.g_lines_read} rows\n")
                        self.g_read_rows = False

                    else:
                        self._extract_column_in(line=line)

                # The COPY command signifies the start of the data for a table,
                # and lists the column names. Data follows...
                elif line.startswith("COPY "):
                    matched_line = re.match(r"^COPY (\w+) \((.*)\)", line)
                    if matched_line:
                        try:
                            self._extract_start_table(matched_line=matched_line)
                        except Exception:
                            continue
                        self._start_table(self.g_table_name)

                elif line.startswith("-- Completed"):
                    logging.info("End of backup")
                    break