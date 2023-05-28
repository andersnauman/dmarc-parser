#!/usr/bin/env python3
# -*- coding: utf-8 -*-

""" This is a DMARC parser library """

import io
import logging

from email import message_from_bytes
from email.message import EmailMessage

from base64 import b64decode

from zipfile import ZipFile, BadZipFile
from gzip import GzipFile, BadGzipFile

import xml.etree.ElementTree as elementTree

from .logger import _custom_logger
from .logger import SYSLOG_TO_SCREEN, SYSLOG_TO_FILE

from .report import aggregate_report_from_xml, forensic_report_from_string
from .report import AggregateReport, ForensicReport
from .report import InvalidOrgName, InvalidTime, InvalidForensicSample, UnknownKey

class DmarcParser():
    """
    Public functions:
     - read_file(file: str)
     - extract_report_from_zip(data: io.BytesIO) -> dict
     - extract_report_from_gzip(data: io.BytesIO) -> dict
     - extract_report_from_xml(data: bytes) -> dict
     - extract_report_from_eml(data: bytes) -> dict
     - parse_aggregate_report(self, data: str) -> AggregateReport
     - parse_forensic_report(self, data: str) -> ForensicReport

    Private functions:
     - _get_file_data(data: bytes) -> dict
     - _normalize_xml(xml: str) -> str
    """

    ZIP_SIGNATURE = b"\x50\x4B\x03\x04"
    GZIP_SIGNATURE = b"\x1F\x8B"
    XML_SIGNATURE = b"\x3C\x3F\x78\x6D\x6C\x20"

    # pylint: disable-next=line-too-long
    def __init__(self, logger: logging.Logger = None, log_level: int = logging.INFO):
        if logger is not None:
            self.logger = logger
        else:
            self.logger = _custom_logger(
                log_level=log_level,
                handler=SYSLOG_TO_SCREEN | SYSLOG_TO_FILE,
            )
        self.reports = []

    def read_file(self, path: str):
        """
        Takes a path argument and returns a dictionary of parsed data.
        
        Input: str
        
        Output: 

        """
        self.logger.debug("Reading %s", path)

        if not path.exists() or not path.is_file():
            self.logger.debug("File %s could not be accessed", path)
            return
        try:
            open_file = path.open("rb")
        except FileNotFoundError:
            self.logger.debug("Could not find file %s", path)
            return

        with open_file:
            data = open_file.read()

        raw_reports = self._get_file_data(data)
        #print(raw_reports)
        if not raw_reports:
            return

        for report_type, raw_report in raw_reports.items():
            if "aggregate" in report_type:
                try:
                    self.reports.append({
                        "type": "aggregate",
                        "report": self.parse_aggregate_report(raw_report),
                    })
                except (InvalidOrgName, InvalidTime) as _error:
                    self.logger.debug("ERROR: %s", _error)
                    continue
            elif "forensic" in report_type:
                try:
                    self.reports.append({
                        "type": "forensic",
                        "report": self.parse_forensic_report(raw_report),
                    })
                except (InvalidForensicSample, UnknownKey) as _error:
                    self.logger.debug("ERROR: %s", _error)
                    continue

    def extract_report_from_zip(self, data: io.BytesIO) -> dict:
        """
        Unzip the content from bytes.
        
        Input: io.BytesIO
        
        Output: dict {"aggregate": {"report": ...}} or None
        
        """
        xml = None
        try:
            zip_file = ZipFile(data)
        except BadZipFile:
            self.logger.debug("Extract ZIP: The data is not ZIP")
            return None

        with zip_file:
            for file in zip_file.namelist():
                try:
                    data = zip_file.open(file, "r")
                except FileNotFoundError:
                    return None
                with data:
                    xml = data.read()
                    # Should never be more than one file so lets break.
                    break
        try:
            xml = xml.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            self.logger.debug("Extract ZIP: Could not decode file")
            xml = None
        else:
            xml = self._normalize_xml(xml)

        return {"aggregate": {"report": xml}}

    def extract_report_from_gzip(self, data: io.BytesIO) -> dict:
        """
        Unzip the content from bytes.
        
        Input: io.BytesIO
        
        Output: dict {"aggregate": {"report": ...}} or None
        
        """
        xml = None
        try:
            gzip_file = GzipFile(data)
        except BadGzipFile:
            self.logger.debug("Extract GZIP: The data is not GZIP")
            return None
        except EOFError:
            self.logger.debug("Extract GZIP: Not all data received?")
            return None

        with gzip_file:
            xml = gzip_file.read()

        try:
            xml = xml.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            self.logger.debug("Extract GZIP: Could not decode file")
            xml = None
        else:
            xml = self._normalize_xml(xml)

        return {"aggregate": {"report": xml}}

    def extract_report_from_xml(self, data: bytes) -> dict:
        """
        Tries to extract xml from bytes.
        
        Input: bytes
        
        Output: dict {"aggregate": {"report": ...}} or None
        
        """
        xml = None
        if isinstance(data, str):
            xml = data
        else:
            try:
                xml = data.decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                self.logger.debug("Extract XML: Could not decode file")
                return None

            xml = self._normalize_xml(xml)

        # Try parsing XML-data. Assume it is an E-mail file if it breaks.
        try:
            elementTree.fromstring(xml)
        except elementTree.ParseError:
            self.logger.debug("Extract XML: Attached file is not a XML")
            return None

        return {"aggregate": {"report": xml}}

    # pylint: disable-next=too-many-branches
    def extract_report_from_eml(self, data: bytes) -> dict:
        """
        Tries to parse the raw text as EML.
        Extracts the attachments and then tries to extract the xml-data.

        Input: bytes

        Output: dict {"aggregate": {"report": ...}, "forensic": {"report": ..., "sample": ...}}

        """
        output = {}
        report_type = None

        data = data.encode("utf-8") if not isinstance(data, bytes) else data
        msg = message_from_bytes(data, _class=EmailMessage)

        for attachment in msg.iter_attachments():
            content_type = attachment.get_content_type()
            payload = attachment.get_payload()

            if isinstance(payload, list):
                # Since we iter through attachments, we could get away with assuming [0].
                # Might regret this.
                payload = payload[0].get_payload()

            file_encoding = attachment.get("content-transfer-encoding")

            if file_encoding and file_encoding.lower() == "base64":
                payload = payload.encode("ascii")
                payload = b64decode(payload)

            if content_type == "message/feedback-report":
                report_type = "forensic"

                try:
                    payload = payload.decode("utf-8") if isinstance(payload, bytes) else payload
                except UnicodeDecodeError:
                    self.logger.debug("message/feedback-report could not be decoded to UTF-8")
                    continue

                if report_type not in output:
                    output[report_type] = {"report": payload}
                else:
                    output[report_type]["report"] =  payload

            elif content_type == "message/rfc822":
                report_type = "forensic"

                try:
                    payload = payload.decode("utf-8") if isinstance(payload, bytes) else payload
                except UnicodeDecodeError:
                    self.logger.debug("message/rfc822 could not be decoded to UTF-8")
                    continue

                if report_type not in output:
                    output[report_type] = {"sample": payload}
                else:
                    output[report_type]["sample"] = payload

            elif content_type.startswith("application/"):
                reports = self._get_file_data(payload)
                for report, payload in reports.items():
                    if not report:
                        continue
                    output[report] = payload

        return output

    def parse_aggregate_report(self, report: dict) -> AggregateReport:
        """
        Parse the aggregate report.
        
        Input: dict {"report": ...}
        
        Output: AggregateReport object        
        """

        if "report" not in report:
            return None

        xml = report["report"]
        try:
            xml = xml.encode("utf-8") if not isinstance(xml, bytes) else xml
        except (UnicodeDecodeError, AttributeError):
            self.logger.debug("Could not decode xml")

        return aggregate_report_from_xml(xml)

    def parse_forensic_report(self, report: dict) -> ForensicReport:
        """
        Parse the forensic report and sample
        
        Input: dict {"report": ..., "sample": ...}
        
        Output: ForensicReport object        
        """

        if "report" in report and "sample" in report:
            return None

        return forensic_report_from_string(
            report["forensic"]["report"],
            report["forensic"]["sample"],
        )

    def _get_file_data(self, data: bytes) -> dict:
        """ Guesses the signature and then extract the unparsed / raw reports """
        reports = None
        if data.startswith(self.ZIP_SIGNATURE):
            reports = self.extract_report_from_zip(io.BytesIO(data))
        elif data.startswith(self.GZIP_SIGNATURE):
            reports = self.extract_report_from_gzip(io.BytesIO(data))
        elif data.lstrip().startswith(self.XML_SIGNATURE):
            reports = self.extract_report_from_xml(data)
        else:
            reports = self.extract_report_from_eml(data)
        return reports

    def _normalize_xml(self, xml: str) -> str:
        """ Normalize the xml. Remove newlines and strip white spaces """
        return "".join(s.strip() for s in xml.splitlines())
