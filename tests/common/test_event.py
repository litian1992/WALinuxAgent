# coding=utf-8
#
# Copyright 2017 Microsoft Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Requires Python 2.6+ and Openssl 1.0+
#
from __future__ import print_function

import json
import os
import platform
import re
import shutil
import threading
import xml.dom
from datetime import datetime, timedelta

from mock import MagicMock

from azurelinuxagent.common.utils import textutil, fileutil, timeutil
from azurelinuxagent.common import event, logger
from azurelinuxagent.common.AgentGlobals import AgentGlobals
from azurelinuxagent.common.event import add_event, add_periodic, add_log_event, elapsed_milliseconds, \
    WALAEventOperation, parse_xml_event, parse_json_event, AGENT_EVENT_FILE_EXTENSION, EVENTS_DIRECTORY, \
    TELEMETRY_EVENT_EVENT_ID, TELEMETRY_EVENT_PROVIDER_ID, TELEMETRY_LOG_EVENT_ID, TELEMETRY_LOG_PROVIDER_ID, \
    report_metric
from azurelinuxagent.common.future import ustr, UTC
from azurelinuxagent.common.osutil import get_osutil
from azurelinuxagent.common.telemetryevent import CommonTelemetryEventSchema, GuestAgentGenericLogsSchema, \
    GuestAgentExtensionEventsSchema, GuestAgentPerfCounterEventsSchema
from azurelinuxagent.common.version import CURRENT_AGENT, CURRENT_VERSION, AGENT_EXECUTION_MODE
from azurelinuxagent.ga.collect_telemetry_events import _CollectAndEnqueueEvents
from tests.lib import wire_protocol_data
from tests.lib.mock_wire_protocol import mock_wire_protocol, MockHttpResponse
from tests.lib.http_request_predicates import HttpRequestPredicates
from tests.lib.tools import AgentTestCase, data_dir, load_data, patch, skip_if_predicate_true
from tests.lib.event_logger_tools import EventLoggerTools


class TestEvent(HttpRequestPredicates, AgentTestCase):
    # These are the Operation/Category for events produced by the tests below (as opposed by events produced by the agent itself)
    _Message = "ThisIsATestEventMessage"
    _Operation = "ThisIsATestEventOperation"
    _Category = "ThisIsATestMetricCategory"

    def setUp(self):
        AgentTestCase.setUp(self)

        self.event_dir = os.path.join(self.tmp_dir, EVENTS_DIRECTORY)
        EventLoggerTools.initialize_event_logger(self.event_dir)
        threading.current_thread().name = "TestEventThread"
        osutil = get_osutil()

        self.expected_common_parameters = {
            # common parameters computed at event creation; the timestamp (stored as the opcode name) is not included
            # here and is checked separately from these parameters
            CommonTelemetryEventSchema.GAVersion: CURRENT_AGENT,
            CommonTelemetryEventSchema.ContainerId: AgentGlobals.get_container_id(),
            CommonTelemetryEventSchema.EventTid: threading.current_thread().ident,
            CommonTelemetryEventSchema.EventPid: os.getpid(),
            CommonTelemetryEventSchema.TaskName: threading.current_thread().name,
            CommonTelemetryEventSchema.KeywordName: json.dumps({"CpuArchitecture": platform.machine()}),
            # common parameters computed from the OS platform
            CommonTelemetryEventSchema.OSVersion: EventLoggerTools.get_expected_os_version(),
            CommonTelemetryEventSchema.ExecutionMode: AGENT_EXECUTION_MODE,
            CommonTelemetryEventSchema.RAM: int(osutil.get_total_mem()),
            CommonTelemetryEventSchema.Processors: osutil.get_processor_cores(),
            # common parameters from the goal state
            CommonTelemetryEventSchema.TenantName: 'db00a7755a5e4e8a8fe4b19bc3b330c3',
            CommonTelemetryEventSchema.RoleName: 'MachineRole',
            CommonTelemetryEventSchema.RoleInstanceName: 'b61f93d0-e1ed-40b2-b067-22c243233448.MachineRole_IN_0',
            # common parameters
            CommonTelemetryEventSchema.Location: EventLoggerTools.mock_imds_data['location'],
            CommonTelemetryEventSchema.SubscriptionId: EventLoggerTools.mock_imds_data['subscriptionId'],
            CommonTelemetryEventSchema.ResourceGroupName: EventLoggerTools.mock_imds_data['resourceGroupName'],
            CommonTelemetryEventSchema.VMId: EventLoggerTools.mock_imds_data['vmId'],
            CommonTelemetryEventSchema.ImageOrigin: EventLoggerTools.mock_imds_data['image_origin'],
        }

        self.expected_extension_events_params = {
            GuestAgentExtensionEventsSchema.IsInternal: False,
            GuestAgentExtensionEventsSchema.ExtensionType: ""
        }

    @staticmethod
    def _report_events(protocol, event_list):
        def _yield_events():
            for telemetry_event in event_list:
                yield telemetry_event

        protocol.client.report_event(_yield_events())

    @staticmethod
    def _collect_events():
        def append_event(e):
            for p in e.parameters:
                if p.name == 'Operation' and p.value == TestEvent._Operation \
                    or p.name == 'Category' and p.value == TestEvent._Category \
                    or p.name == 'Message' and p.value == TestEvent._Message \
                    or p.name == 'Context1' and p.value == TestEvent._Message:
                    event_list.append(e)
        event_list = []
        send_telemetry_events = MagicMock()
        send_telemetry_events.enqueue_event = MagicMock(wraps=append_event)
        event_collector = _CollectAndEnqueueEvents(send_telemetry_events)
        event_collector.process_events()
        return event_list

    def _collect_event_files(self):
        files = [os.path.join(self.event_dir, f) for f in os.listdir(self.event_dir)]
        return [f for f in files if fileutil.findre_in_file(f, TestEvent._Operation)]

    @staticmethod
    def _is_guest_extension_event(event):  # pylint: disable=redefined-outer-name
        return event.eventId == TELEMETRY_EVENT_EVENT_ID and event.providerId == TELEMETRY_EVENT_PROVIDER_ID

    @staticmethod
    def _is_telemetry_log_event(event):  # pylint: disable=redefined-outer-name
        return event.eventId == TELEMETRY_LOG_EVENT_ID and event.providerId == TELEMETRY_LOG_PROVIDER_ID

    def test_parse_xml_event(self, *args):  # pylint: disable=unused-argument
        data_str = load_data('ext/event_from_extension.xml')
        event = parse_xml_event(data_str)  # pylint: disable=redefined-outer-name
        self.assertIsNotNone(event)
        self.assertNotEqual(0, event.parameters)
        self.assertTrue(all(param is not None for param in event.parameters))

    def test_parse_json_event(self, *args):  # pylint: disable=unused-argument
        data_str = load_data('ext/event.json')
        event = parse_json_event(data_str)  # pylint: disable=redefined-outer-name
        self.assertIsNotNone(event)
        self.assertNotEqual(0, event.parameters)
        self.assertTrue(all(param is not None for param in event.parameters))

    def test_add_event_should_use_the_container_id_from_the_most_recent_goal_state(self):
        def create_event_and_return_container_id():  # pylint: disable=inconsistent-return-statements
            event.add_event(name='Event', op=TestEvent._Operation)
            event_list = self._collect_events()
            self.assertEqual(len(event_list), 1, "Could not find the event created by add_event")

            for p in event_list[0].parameters:
                if p.name == CommonTelemetryEventSchema.ContainerId:
                    return p.value

            self.fail("Could not find Contained ID on event")

        with mock_wire_protocol(wire_protocol_data.DATA_FILE) as protocol:
            contained_id = create_event_and_return_container_id()
            # The expect value comes from DATA_FILE
            self.assertEqual(contained_id, 'c6d5526c-5ac2-4200-b6e2-56f2b70c5ab2', "Incorrect container ID")

            protocol.mock_wire_data.set_container_id('AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE')
            protocol.client.update_goal_state()
            contained_id = create_event_and_return_container_id()
            self.assertEqual(contained_id, 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE', "Incorrect container ID")

            protocol.mock_wire_data.set_container_id('11111111-2222-3333-4444-555555555555')
            protocol.client.update_goal_state()
            contained_id = create_event_and_return_container_id()
            self.assertEqual(contained_id, '11111111-2222-3333-4444-555555555555', "Incorrect container ID")

    def test_add_event_should_handle_event_errors(self):
        with patch("azurelinuxagent.common.utils.fileutil.mkdir", side_effect=OSError):
            with patch('azurelinuxagent.common.logger.periodic_error') as mock_logger_periodic_error:
                add_event('test', message='test event', op=TestEvent._Operation)

                # The event shouldn't have been created
                self.assertTrue(len(self._collect_event_files()) == 0)

                # The exception should have been caught and logged
                args = mock_logger_periodic_error.call_args
                exception_message = args[0][1]
                self.assertIn("[EventError] Failed to create events folder", exception_message)

    def test_event_status_event_marked(self):
        es = event.__event_status__

        self.assertFalse(es.event_marked("Foo", "1.2", "FauxOperation"))
        es.mark_event_status("Foo", "1.2", "FauxOperation", True)
        self.assertTrue(es.event_marked("Foo", "1.2", "FauxOperation"))

        event.__event_status__ = event.EventStatus()
        event.init_event_status(self.tmp_dir)
        es = event.__event_status__
        self.assertTrue(es.event_marked("Foo", "1.2", "FauxOperation"))

    def test_event_status_defaults_to_success(self):
        es = event.__event_status__
        self.assertTrue(es.event_succeeded("Foo", "1.2", "FauxOperation"))

    def test_event_status_records_status(self):
        es = event.EventStatus()

        es.mark_event_status("Foo", "1.2", "FauxOperation", True)
        self.assertTrue(es.event_succeeded("Foo", "1.2", "FauxOperation"))

        es.mark_event_status("Foo", "1.2", "FauxOperation", False)
        self.assertFalse(es.event_succeeded("Foo", "1.2", "FauxOperation"))

    def test_event_status_preserves_state(self):
        es = event.__event_status__

        es.mark_event_status("Foo", "1.2", "FauxOperation", False)
        self.assertFalse(es.event_succeeded("Foo", "1.2", "FauxOperation"))

        event.__event_status__ = event.EventStatus()
        event.init_event_status(self.tmp_dir)
        es = event.__event_status__
        self.assertFalse(es.event_succeeded("Foo", "1.2", "FauxOperation"))

    def test_should_emit_event_ignores_unknown_operations(self):
        event.__event_status__ = event.EventStatus()

        self.assertTrue(event.should_emit_event("Foo", "1.2", "FauxOperation", True))
        self.assertTrue(event.should_emit_event("Foo", "1.2", "FauxOperation", False))

        # Marking the event has no effect
        event.mark_event_status("Foo", "1.2", "FauxOperation", True)

        self.assertTrue(event.should_emit_event("Foo", "1.2", "FauxOperation", True))
        self.assertTrue(event.should_emit_event("Foo", "1.2", "FauxOperation", False))

    def test_should_emit_event_handles_known_operations(self):
        event.__event_status__ = event.EventStatus()

        # Known operations always initially "fire"
        for op in event.__event_status_operations__:
            self.assertTrue(event.should_emit_event("Foo", "1.2", op, True))
            self.assertTrue(event.should_emit_event("Foo", "1.2", op, False))

        # Note a success event...
        for op in event.__event_status_operations__:
            event.mark_event_status("Foo", "1.2", op, True)

        # Subsequent success events should not fire, but failures will
        for op in event.__event_status_operations__:
            self.assertFalse(event.should_emit_event("Foo", "1.2", op, True))
            self.assertTrue(event.should_emit_event("Foo", "1.2", op, False))

        # Note a failure event...
        for op in event.__event_status_operations__:
            event.mark_event_status("Foo", "1.2", op, False)

        # Subsequent success events fire and failure do not
        for op in event.__event_status_operations__:
            self.assertTrue(event.should_emit_event("Foo", "1.2", op, True))
            self.assertFalse(event.should_emit_event("Foo", "1.2", op, False))

    @patch('azurelinuxagent.common.event.EventLogger')
    @patch('azurelinuxagent.common.logger.error')
    @patch('azurelinuxagent.common.logger.warn')
    @patch('azurelinuxagent.common.logger.info')
    def test_should_log_errors_if_failed_operation_and_empty_event_dir(self,
                                                                       mock_logger_info,
                                                                       mock_logger_warn,
                                                                       mock_logger_error,
                                                                       mock_reporter):
        mock_reporter.event_dir = None
        add_event("dummy name",
                  version=CURRENT_VERSION,
                  op=WALAEventOperation.Download,
                  is_success=False,
                  message="dummy event message",
                  reporter=mock_reporter)

        self.assertEqual(1, mock_logger_error.call_count)
        self.assertEqual(1, mock_logger_warn.call_count)
        self.assertEqual(0, mock_logger_info.call_count)

        args = mock_logger_error.call_args[0]
        self.assertEqual(('dummy name', 'Download', 'dummy event message', 0), args[1:])

    @patch('azurelinuxagent.common.event.EventLogger')
    @patch('azurelinuxagent.common.logger.error')
    @patch('azurelinuxagent.common.logger.warn')
    @patch('azurelinuxagent.common.logger.info')
    def test_should_log_errors_if_failed_operation_and_not_empty_event_dir(self,
                                                                           mock_logger_info,
                                                                           mock_logger_warn,
                                                                           mock_logger_error,
                                                                           mock_reporter):
        mock_reporter.event_dir = "dummy"

        with patch("azurelinuxagent.common.event.should_emit_event", return_value=True) as mock_should_emit_event:
            with patch("azurelinuxagent.common.event.mark_event_status"):
                with patch("azurelinuxagent.common.event.EventLogger._add_event"):
                    add_event("dummy name",
                              version=CURRENT_VERSION,
                              op=WALAEventOperation.Download,
                              is_success=False,
                              message="dummy event message")

                    self.assertEqual(1, mock_should_emit_event.call_count)
                    self.assertEqual(1, mock_logger_error.call_count)
                    self.assertEqual(0, mock_logger_warn.call_count)
                    self.assertEqual(0, mock_logger_info.call_count)

                    args = mock_logger_error.call_args[0]
                    self.assertEqual(('dummy name', 'Download', 'dummy event message', 0), args[1:])

    @patch('azurelinuxagent.common.event.EventLogger.add_event')
    def test_periodic_emits_if_not_previously_sent(self, mock_event):
        event.__event_logger__.reset_periodic()

        event.add_periodic(logger.EVERY_DAY, "FauxEvent")
        self.assertEqual(1, mock_event.call_count)

    @patch('azurelinuxagent.common.event.EventLogger.add_event')
    def test_periodic_does_not_emit_if_previously_sent(self, mock_event):
        event.__event_logger__.reset_periodic()

        event.add_periodic(logger.EVERY_DAY, "FauxEvent")
        self.assertEqual(1, mock_event.call_count)

        event.add_periodic(logger.EVERY_DAY, "FauxEvent")
        self.assertEqual(1, mock_event.call_count)

    @patch('azurelinuxagent.common.event.EventLogger.add_event')
    def test_periodic_emits_if_forced(self, mock_event):
        event.__event_logger__.reset_periodic()

        event.add_periodic(logger.EVERY_DAY, "FauxEvent")
        self.assertEqual(1, mock_event.call_count)

        event.add_periodic(logger.EVERY_DAY, "FauxEvent", force=True)
        self.assertEqual(2, mock_event.call_count)

    @patch('azurelinuxagent.common.event.EventLogger.add_event')
    def test_periodic_emits_after_elapsed_delta(self, mock_event):
        event.__event_logger__.reset_periodic()

        event.add_periodic(logger.EVERY_DAY, "FauxEvent")
        self.assertEqual(1, mock_event.call_count)

        event.add_periodic(logger.EVERY_DAY, "FauxEvent")
        self.assertEqual(1, mock_event.call_count)

        h = hash("FauxEvent"+WALAEventOperation.Unknown+ustr(True))
        event.__event_logger__.periodic_events[h] = \
            datetime.now(UTC) - logger.EVERY_DAY - logger.EVERY_HOUR
        event.add_periodic(logger.EVERY_DAY, "FauxEvent")
        self.assertEqual(2, mock_event.call_count)

    @patch('azurelinuxagent.common.event.EventLogger.add_event')
    def test_periodic_forwards_args(self, mock_event):
        event.__event_logger__.reset_periodic()
        event.add_periodic(logger.EVERY_DAY, "FauxEvent", op=WALAEventOperation.Log, is_success=True, duration=0,
                           version=str(CURRENT_VERSION), message="FauxEventMessage", log_event=True, force=False)
        mock_event.assert_called_once_with("FauxEvent", op=WALAEventOperation.Log, is_success=True, duration=0,
                                           version=str(CURRENT_VERSION), message="FauxEventMessage", log_event=True)

    @patch("azurelinuxagent.common.event.datetime")
    @patch('azurelinuxagent.common.event.EventLogger.add_event')
    def test_periodic_forwards_args_default_values(self, mock_event, mock_datetime):  # pylint: disable=unused-argument
        event.__event_logger__.reset_periodic()
        event.add_periodic(logger.EVERY_DAY, "FauxEvent", message="FauxEventMessage")
        mock_event.assert_called_once_with("FauxEvent", op=WALAEventOperation.Unknown, is_success=True, duration=0,
                                           version=str(CURRENT_VERSION), message="FauxEventMessage", log_event=True)

    @patch("azurelinuxagent.common.event.EventLogger.add_event")
    def test_add_event_default_variables(self, mock_add_event):
        add_event('test', message='test event')
        mock_add_event.assert_called_once_with('test', duration=0, is_success=True, log_event=True,
                                               message='test event', op=WALAEventOperation.Unknown,
                                               version=str(CURRENT_VERSION), flush=False)

    def test_collect_events_should_delete_event_files(self):
        add_event(name='Event1', op=TestEvent._Operation)
        add_event(name='Event1', op=TestEvent._Operation)
        add_event(name='Event3', op=TestEvent._Operation)

        event_files = self._collect_event_files()
        self.assertEqual(3, len(event_files), "Did not find all the event files that were created")

        event_list = self._collect_events()
        event_files = os.listdir(self.event_dir)

        self.assertEqual(len(event_list), 3, "Did not collect all the events that were created")
        self.assertEqual(len(event_files), 0, "The event files were not deleted")

    def test_save_event(self):
        add_event('test', message='test event', op=TestEvent._Operation)
        self.assertTrue(len(self._collect_event_files()) == 1)

        # checking the extension of the file created.
        for filename in os.listdir(self.event_dir):
            self.assertTrue(filename.endswith(AGENT_EVENT_FILE_EXTENSION),
                'Event file does not have the correct extension ({0}): {1}'.format(AGENT_EVENT_FILE_EXTENSION, filename))

    def test_save_event_redact_sas_token(self):
        add_event('test', message='test event with sas token: https://test.blob.core.windows.net/$system/lrwinmcdn_0.0f3bfecf-f14f-4c7d-8275-9dee7310fe8c.vmSettings?sv=2018-03-28&amp;sr=b&amp;sk=system-1&amp;sig=8YHwmibhasT0r9MZgL09QmFwL7ZV%2bg%2b49QP5Zwe4ksY%3d&amp;se=9999-01-01T00%3a00%3a00Z&amp;sp=r', op=TestEvent._Operation)
        event_files = self._collect_event_files()
        self.assertTrue(len(event_files) == 1)

        first_event = event_files[0]
        with open(first_event) as first_fh:
            first_event_text = first_fh.read()
            self.assertTrue('<redacted>' in first_event_text)

    def test_add_event_flush_immediately(self):
        def http_post_handler(url, body, **__):
            if self.is_telemetry_request(url):
                http_post_handler.request_body = body
                return MockHttpResponse(status=200)
            return None
        http_post_handler.request_body = None

        with mock_wire_protocol(wire_protocol_data.DATA_FILE, http_post_handler=http_post_handler):
            expected_message = 'test event'
            add_event('test', message=expected_message, op=TestEvent._Operation, flush=True)

            event_message = self._get_event_message_from_http_request_body(http_post_handler.request_body)

            self.assertEqual(event_message, expected_message,
                         "The Message in the HTTP request does not match the Message in the add_event")

            # If immediate_flush is set, the event should send to wireserver directly and file should not be created
            self.assertTrue(len(self._collect_event_files()) == 0)

    def test_add_event_flush_fails(self):
        def http_post_handler(url, **__):
            if self.is_telemetry_request(url):
                return MockHttpResponse(status=500)
            return None

        with mock_wire_protocol(wire_protocol_data.DATA_FILE, http_post_handler=http_post_handler):
            expected_message = 'test event'
            add_event('test', message=expected_message, op=TestEvent._Operation, flush=True)

            # In case of failure, the event file should be created
            self.assertTrue(len(self._collect_event_files()) == 1)

    @staticmethod
    def _get_event_message(evt):
        for p in evt.parameters:
            if p.name == GuestAgentExtensionEventsSchema.Message:
                return p.value
        return None

    def test_collect_events_should_be_able_to_process_events_with_non_ascii_characters(self):
        self._create_test_event_file("custom_script_nonascii_characters.tld")

        event_list = self._collect_events()

        self.assertEqual(len(event_list), 1)
        self.assertEqual(TestEvent._get_event_message(event_list[0]), u'World\u05e2\u05d9\u05d5\u05ea \u05d0\u05d7\u05e8\u05d5\u05ea\u0906\u091c')

    def test_collect_events_should_redact_message(self):
        self._create_test_event_file("event_with_sas_token.tld")

        event_list = self._collect_events()

        self.assertEqual(len(event_list), 1)

        self.assertIn('<redacted>', TestEvent._get_event_message(event_list[0]))

    def test_collect_events_should_ignore_invalid_event_files(self):
        self._create_test_event_file("custom_script_1.tld")  # a valid event
        self._create_test_event_file("custom_script_utf-16.tld")
        self._create_test_event_file("custom_script_invalid_json.tld")
        os.chmod(self._create_test_event_file("custom_script_no_read_access.tld"), 0o200)
        self._create_test_event_file("custom_script_2.tld")  # another valid event

        with patch("azurelinuxagent.common.event.add_event") as mock_add_event:
            # mock the max retries on parsing invalid json to avoid the test run delays
            with patch("azurelinuxagent.ga.collect_telemetry_events.NUM_OF_EVENT_FILE_RETRIES", 1):
                event_list = self._collect_events()

                self.assertEqual(
                    len(event_list), 2)
                self.assertTrue(
                    all(TestEvent._get_event_message(evt) == "A test telemetry message." for evt in event_list),
                    "The valid events were not found")

                invalid_events = []
                total_dropped_count = 0
                for args, kwargs in mock_add_event.call_args_list:  # pylint: disable=unused-variable
                    match = re.search(r"DroppedEventsCount: (\d+)", kwargs['message'])
                    if match is not None:
                        invalid_events.append(kwargs['op'])
                        total_dropped_count += int(match.groups()[0])

                self.assertEqual(3, total_dropped_count, "Total dropped events dont match")
                self.assertIn(WALAEventOperation.CollectEventErrors, invalid_events,
                              "{0} errors not reported".format(WALAEventOperation.CollectEventErrors))
                self.assertIn(WALAEventOperation.CollectEventUnicodeErrors, invalid_events,
                              "{0} errors not reported".format(WALAEventOperation.CollectEventUnicodeErrors))

    def test_save_event_rollover(self):
        # We keep 1000 events only, and the older ones are removed.

        num_of_events = 999
        add_event('test', message='first event')  # this makes number of events to num_of_events + 1.
        for i in range(num_of_events):
            add_event('test', message='test event {0}'.format(i))

        num_of_events += 1 # adding the first add_event.

        events = os.listdir(self.event_dir)
        events.sort()
        self.assertTrue(len(events) == num_of_events, "{0} is not equal to {1}".format(len(events), num_of_events))

        first_event = os.path.join(self.event_dir, events[0])
        with open(first_event) as first_fh:
            first_event_text = first_fh.read()
            self.assertTrue('first event' in first_event_text)

        add_event('test', message='last event')
        # Adding the above event displaces the first_event

        events = os.listdir(self.event_dir)
        events.sort()
        self.assertTrue(len(events) == num_of_events,
                        "{0} events found, {1} expected".format(len(events), num_of_events))

        first_event = os.path.join(self.event_dir, events[0])
        with open(first_event) as first_fh:
            first_event_text = first_fh.read()
            self.assertFalse('first event' in first_event_text, "'first event' not in {0}".format(first_event_text))
            self.assertTrue('test event 0' in first_event_text)

        last_event = os.path.join(self.event_dir, events[-1])
        with open(last_event) as last_fh:
            last_event_text = last_fh.read()
            self.assertTrue('last event' in last_event_text)

    def test_save_event_cleanup(self):
        for i in range(0, 2000):
            evt = os.path.join(self.event_dir, '{0}.tld'.format(ustr(1491004920536531 + i)))
            with open(evt, 'w') as fh:
                fh.write('{0}{1}'.format(TestEvent._Operation, i))

        test_events = self._collect_event_files()
        self.assertTrue(len(test_events) == 2000, "{0} events found, 2000 expected".format(len(test_events)))

        add_event('test', message='last event', op=TestEvent._Operation)

        events = os.listdir(self.event_dir)
        self.assertTrue(len(events) == 1000, "{0} events found, 1000 expected".format(len(events)))

    def test_elapsed_milliseconds(self):
        utc_start = datetime.now(UTC) + timedelta(days=1)
        self.assertEqual(0, elapsed_milliseconds(utc_start))

    def _assert_event_includes_all_parameters_in_the_telemetry_schema(self, actual_event, expected_parameters, assert_timestamp):
        # add the common parameters to the set of expected parameters
        all_expected_parameters = self.expected_common_parameters.copy()
        if self._is_guest_extension_event(actual_event):
            all_expected_parameters.update(self.expected_extension_events_params.copy())
        all_expected_parameters.update(expected_parameters)

        # convert the event parameters to a dictionary; do not include the timestamp,
        # which is verified using assert_timestamp()
        event_parameters = {}
        timestamp = None
        for p in actual_event.parameters:
            if p.name == CommonTelemetryEventSchema.OpcodeName:  # the timestamp is stored in the opcode name
                timestamp = p.value
            else:
                event_parameters[p.name] = p.value

        if self._is_telemetry_log_event(actual_event):
            # Remove Context2 from event parameters and verify that the timestamp is correct
            telemetry_log_event_timestamp = event_parameters.pop(GuestAgentGenericLogsSchema.Context2, None)
            self.assertIsNotNone(telemetry_log_event_timestamp, "Context2 should be filled with a timestamp")
            assert_timestamp(telemetry_log_event_timestamp)

        self.maxDiff = None  # the dictionary diffs can be quite large; display the whole thing
        self.assertDictEqual(event_parameters, all_expected_parameters)

        self.assertIsNotNone(timestamp, "The event does not have a timestamp (Opcode)")
        assert_timestamp(timestamp)

    def _test_create_event_function_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(self, create_event_function, expected_parameters):
        """
        Helper to tests methods that create events (e.g. add_event, add_log_event, etc).
        """
        # execute the method that creates the event, capturing the time range of the execution
        timestamp_lower = timeutil.create_utc_timestamp(datetime.now(UTC))
        create_event_function()
        timestamp_upper = timeutil.create_utc_timestamp(datetime.now(UTC))

        event_list = self._collect_events()

        self.assertEqual(len(event_list), 1)

        # verify the event parameters
        self._assert_event_includes_all_parameters_in_the_telemetry_schema(
            event_list[0],
            expected_parameters,
            assert_timestamp=lambda timestamp:
                self.assertTrue(timestamp_lower <= timestamp <= timestamp_upper, "The event timestamp (opcode) is incorrect")
        )

    def test_add_event_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(self):
        self._test_create_event_function_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(
            create_event_function=lambda:
                add_event(
                    name="TestEvent",
                    op=TestEvent._Operation,
                    is_success=True,
                    duration=1234,
                    version="1.2.3.4",
                    message="Test Message"),
            expected_parameters={
                GuestAgentExtensionEventsSchema.Name: 'TestEvent',
                GuestAgentExtensionEventsSchema.Version: '1.2.3.4',
                GuestAgentExtensionEventsSchema.Operation: TestEvent._Operation,
                GuestAgentExtensionEventsSchema.OperationSuccess: True,
                GuestAgentExtensionEventsSchema.Message: 'Test Message',
                GuestAgentExtensionEventsSchema.Duration: 1234,
                GuestAgentExtensionEventsSchema.ExtensionType: ''})

    def test_add_periodic_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(self):
        self._test_create_event_function_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(
            create_event_function=lambda:
                add_periodic(
                    delta=logger.EVERY_MINUTE,
                    name="TestPeriodicEvent",
                    op=TestEvent._Operation,
                    is_success=False,
                    duration=4321,
                    version="4.3.2.1",
                    message="Test Periodic Message"),
            expected_parameters={
                GuestAgentExtensionEventsSchema.Name: 'TestPeriodicEvent',
                GuestAgentExtensionEventsSchema.Version: '4.3.2.1',
                GuestAgentExtensionEventsSchema.Operation: TestEvent._Operation,
                GuestAgentExtensionEventsSchema.OperationSuccess: False,
                GuestAgentExtensionEventsSchema.Message: 'Test Periodic Message',
                GuestAgentExtensionEventsSchema.Duration: 4321,
                GuestAgentExtensionEventsSchema.ExtensionType: ''})

    @skip_if_predicate_true(lambda: True, "Enable this test when SEND_LOGS_TO_TELEMETRY is enabled")
    def test_add_log_event_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(self):
        self._test_create_event_function_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(
            create_event_function=lambda: add_log_event(logger.LogLevel.INFO, 'A test INFO log event'),
            expected_parameters={
                GuestAgentGenericLogsSchema.EventName: 'Log',
                GuestAgentGenericLogsSchema.CapabilityUsed: 'INFO',
                GuestAgentGenericLogsSchema.Context1: 'log event',
                GuestAgentGenericLogsSchema.Context3: ''
            })

    def test_add_log_event_should_always_create_events_when_forced(self):
        self._test_create_event_function_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(
            create_event_function=lambda: add_log_event(logger.LogLevel.WARNING, TestEvent._Message,
                                                        forced=True),
            expected_parameters={
                GuestAgentGenericLogsSchema.EventName: 'Log',
                GuestAgentGenericLogsSchema.CapabilityUsed: 'WARNING',
                GuestAgentGenericLogsSchema.Context1: TestEvent._Message,
                GuestAgentGenericLogsSchema.Context3: ''
            })

    def test_add_log_event_should_not_create_event_if_not_allowed_and_not_forced(self):
        add_log_event(logger.LogLevel.WARNING, 'A test WARNING log event')
        event_list = self._collect_events()
        self.assertEqual(len(event_list), 0, "No events should be created if not forced and not allowed")

    def test_report_metric_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(self):
        self._test_create_event_function_should_create_events_that_have_all_the_parameters_in_the_telemetry_schema(
            create_event_function=lambda: report_metric(TestEvent._Category, "%idle", "total", 12.34),
            expected_parameters={
                GuestAgentPerfCounterEventsSchema.Category: TestEvent._Category,
                GuestAgentPerfCounterEventsSchema.Counter: '%idle',
                GuestAgentPerfCounterEventsSchema.Instance: 'total',
                GuestAgentPerfCounterEventsSchema.Value: 12.34
            })

    def _create_test_event_file(self, source_file):
        source_file_path = os.path.join(data_dir, "events", source_file)
        target_file_path = os.path.join(self.event_dir, source_file)
        shutil.copy(source_file_path, target_file_path)
        return target_file_path

    def _collect_test_event_files(self, file_name):
        return [os.path.join(self.event_dir, f) for f in os.listdir(self.event_dir) if file_name in f]

    @staticmethod
    def _get_file_creation_timestamp(file):  # pylint: disable=redefined-builtin
        return  timeutil.create_utc_timestamp(datetime.fromtimestamp(os.path.getmtime(file)).replace(tzinfo=UTC))

    def test_collect_events_should_add_all_the_parameters_in_the_telemetry_schema_to_legacy_agent_events(self):
        # Agents <= 2.2.46 use *.tld as the extension for event files (newer agents use "*.waagent.tld") and they populate
        # only a subset of fields; the rest are added by the current agent when events are collected.
        self._create_test_event_file("legacy_agent.tld")

        event_list = self._collect_events()

        self.assertEqual(len(event_list), 1)

        self._assert_event_includes_all_parameters_in_the_telemetry_schema(
            event_list[0],
            expected_parameters={
                GuestAgentExtensionEventsSchema.Name: "WALinuxAgent",
                GuestAgentExtensionEventsSchema.Version: "9.9.9",
                GuestAgentExtensionEventsSchema.IsInternal: False,
                GuestAgentExtensionEventsSchema.Operation: TestEvent._Operation,
                GuestAgentExtensionEventsSchema.OperationSuccess: True,
                GuestAgentExtensionEventsSchema.Message: "The cgroup filesystem is ready to use",
                GuestAgentExtensionEventsSchema.Duration: 1234,
                GuestAgentExtensionEventsSchema.ExtensionType: "ALegacyExtensionType",
                CommonTelemetryEventSchema.GAVersion: "WALinuxAgent-1.1.1",
                CommonTelemetryEventSchema.ContainerId: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
                CommonTelemetryEventSchema.EventTid: 98765,
                CommonTelemetryEventSchema.EventPid: 4321,
                CommonTelemetryEventSchema.TaskName: "ALegacyTask",
                CommonTelemetryEventSchema.KeywordName: "ALegacyKeywordName"},
            assert_timestamp=lambda timestamp:
                self.assertEqual(timestamp, '1970-01-01 12:00:00', "The event timestamp (opcode) is incorrect")
        )

    def test_collect_events_should_use_the_file_creation_time_for_legacy_agent_events_missing_a_timestamp(self):
        test_file = self._create_test_event_file("legacy_agent_no_timestamp.tld")

        event_creation_time = TestEvent._get_file_creation_timestamp(test_file)

        event_list = self._collect_events()

        self.assertEqual(len(event_list), 1)

        self._assert_event_includes_all_parameters_in_the_telemetry_schema(
            event_list[0],
            expected_parameters={
                GuestAgentExtensionEventsSchema.Name: "WALinuxAgent",
                GuestAgentExtensionEventsSchema.Version: "9.9.9",
                GuestAgentExtensionEventsSchema.IsInternal: False,
                GuestAgentExtensionEventsSchema.Operation: TestEvent._Operation,
                GuestAgentExtensionEventsSchema.OperationSuccess: True,
                GuestAgentExtensionEventsSchema.Message: "The cgroup filesystem is ready to use",
                GuestAgentExtensionEventsSchema.Duration: 1234,
                GuestAgentExtensionEventsSchema.ExtensionType: "ALegacyExtensionType",
                CommonTelemetryEventSchema.GAVersion: "WALinuxAgent-1.1.1",
                CommonTelemetryEventSchema.ContainerId: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
                CommonTelemetryEventSchema.EventTid: 98765,
                CommonTelemetryEventSchema.EventPid: 4321,
                CommonTelemetryEventSchema.TaskName: "ALegacyTask",
                CommonTelemetryEventSchema.KeywordName: "ALegacyKeywordName"},
            assert_timestamp=lambda timestamp:
                self.assertEqual(timestamp, event_creation_time, "The event timestamp (opcode) is incorrect")
        )

    def _assert_extension_event_includes_all_parameters_in_the_telemetry_schema(self, event_file):
        # Extensions drop their events as *.tld files on the events directory. They populate only a subset of fields,
        # and the rest are added by the agent when events are collected.
        test_file = self._create_test_event_file(event_file)

        event_creation_time = TestEvent._get_file_creation_timestamp(test_file)

        event_list = self._collect_events()

        self.assertEqual(len(event_list), 1)

        self._assert_event_includes_all_parameters_in_the_telemetry_schema(
            event_list[0],
            expected_parameters={
                GuestAgentExtensionEventsSchema.Name: 'Microsoft.Azure.Extensions.CustomScript',
                GuestAgentExtensionEventsSchema.Version: '2.0.4',
                GuestAgentExtensionEventsSchema.Operation: TestEvent._Operation,
                GuestAgentExtensionEventsSchema.OperationSuccess: True,
                GuestAgentExtensionEventsSchema.Message: 'A test telemetry message.',
                GuestAgentExtensionEventsSchema.Duration: 150000,
                GuestAgentExtensionEventsSchema.ExtensionType: 'json'},
            assert_timestamp=lambda timestamp:
                self.assertEqual(timestamp, event_creation_time, "The event timestamp (opcode) is incorrect")
            )

    def test_collect_events_should_add_all_the_parameters_in_the_telemetry_schema_to_extension_events(self):
        self._assert_extension_event_includes_all_parameters_in_the_telemetry_schema('custom_script_1.tld')

    def test_collect_events_should_ignore_extra_parameters_in_extension_events(self):
        self._assert_extension_event_includes_all_parameters_in_the_telemetry_schema('custom_script_extra_parameters.tld')

    @staticmethod
    def _get_event_message_from_http_request_body(event_body):
        # The XML for the event is sent over as a CDATA element ("Event") in the request's body
        http_request_body = event_body if (
                event_body is None or type(event_body) is ustr) else textutil.str_to_encoded_ustr(event_body)
        request_body_xml_doc = textutil.parse_doc(http_request_body)

        event_node = textutil.find(request_body_xml_doc, "Event")
        if event_node is None:
            raise ValueError('Could not find the Event node in the XML document')
        if len(event_node.childNodes) != 1:
            raise ValueError('The Event node in the XML document should have exactly 1 child')

        event_node_first_child = event_node.childNodes[0]
        if event_node_first_child.nodeType != xml.dom.Node.CDATA_SECTION_NODE:
            raise ValueError('The Event node contents should be CDATA')

        event_node_cdata = event_node_first_child.nodeValue

        # The CDATA will contain a sequence of "<Param Name='foo' Value='bar'/>" nodes, which
        # correspond to the parameters of the telemetry event.  Wrap those into a "Helper" node
        # and extract the "Message"
        event_xml_text = '<?xml version="1.0"?><Helper>{0}</Helper>'.format(event_node_cdata)
        event_xml_doc = textutil.parse_doc(event_xml_text)
        helper_node = textutil.find(event_xml_doc, "Helper")

        for child in helper_node.childNodes:
            if child.getAttribute('Name') == GuestAgentExtensionEventsSchema.Message:
                return child.getAttribute('Value')

        raise ValueError(
            'Could not find the Message for the telemetry event. Request body: {0}'.format(http_request_body))

    def test_report_event_should_encode_call_stack_correctly(self):
        """
        The Message in some telemetry events that include call stacks are being truncated in Kusto. While the issue doesn't seem
        to be in the agent itself, this test verifies that the Message of the event we send in the HTTP request matches the
        Message we read from the event's file.
        """
        def get_event_message_from_event_file(event_file):
            with open(event_file, "rb") as fd:
                event_data = fd.read().decode("utf-8")  # event files are UTF-8 encoded
            telemetry_event = json.loads(event_data)

            for p in telemetry_event['parameters']:
                if p['name'] == GuestAgentExtensionEventsSchema.Message:
                    return p['value']

            raise ValueError('Could not find the Message for the telemetry event in {0}'.format(event_file))

        def http_post_handler(url, body, **__):
            if self.is_telemetry_request(url):
                http_post_handler.request_body = body
                return MockHttpResponse(status=200)
            return None
        http_post_handler.request_body = None

        with mock_wire_protocol(wire_protocol_data.DATA_FILE, http_post_handler=http_post_handler) as protocol:
            event_file_path = self._create_test_event_file("event_with_callstack.waagent.tld")
            expected_message = get_event_message_from_event_file(event_file_path)

            event_list = self._collect_events()
            self._report_events(protocol, event_list)

            event_message = self._get_event_message_from_http_request_body(http_post_handler.request_body)

            self.assertEqual(event_message, expected_message, "The Message in the HTTP request does not match the Message in the event's *.tld file")

    def test_report_event_should_encode_events_correctly(self):

        def http_post_handler(url, body, **__):
            if self.is_telemetry_request(url):
                http_post_handler.request_body = body
                return MockHttpResponse(status=200)
            return None
        http_post_handler.request_body = None

        with mock_wire_protocol(wire_protocol_data.DATA_FILE, http_post_handler=http_post_handler) as protocol:
            test_messages = [
                'Non-English message -  此文字不是英文的',
                "Ξεσκεπάζω τὴν ψυχοφθόρα βδελυγμία",
                "The quick brown fox jumps over the lazy dog",
                "El pingüino Wenceslao hizo kilómetros bajo exhaustiva lluvia y frío, añoraba a su querido cachorro.",
                "Portez ce vieux whisky au juge blond qui fume sur son île intérieure, à côté de l'alcôve ovoïde, où les bûches",
                "se consument dans l'âtre, ce qui lui permet de penser à la cænogenèse de l'être dont il est question",
                "dans la cause ambiguë entendue à Moÿ, dans un capharnaüm qui, pense-t-il, diminue çà et là la qualité de son œuvre.",
                "D'fhuascail Íosa, Úrmhac na hÓighe Beannaithe, pór Éava agus Ádhaimh",
                "Árvíztűrő tükörfúrógép",
                "Kæmi ný öxi hér ykist þjófum nú bæði víl og ádrepa",
                "Sævör grét áðan því úlpan var ónýt",
                "いろはにほへとちりぬるを わかよたれそつねならむ うゐのおくやまけふこえて あさきゆめみしゑひもせす",
                "? דג סקרן שט בים מאוכזב ולפתע מצא לו חברה איך הקליטה"
                "Pchnąć w tę łódź jeża lub ośm skrzyń fig",
                "Normal string event"
            ]
            for msg in test_messages:
                add_event('TestEventEncoding', message=msg, op=TestEvent._Operation)
                event_list = self._collect_events()
                self._report_events(protocol, event_list)
                # In Py2, encode() produces a str and in py3 it produces a bytes string.
                # type(bytes) == type(str) for Py2 so this check is mainly for Py3 to ensure that the event is encoded properly.
                self.assertIsInstance(http_post_handler.request_body, bytes, "The Event request body should be encoded")
                self.assertIn(textutil.str_to_encoded_ustr(msg).encode('utf-8'), http_post_handler.request_body,
                              "Encoded message not found in body")


class TestMetrics(AgentTestCase):
    @patch('azurelinuxagent.common.event.EventLogger.save_event')
    def test_report_metric(self, mock_event):
        event.report_metric("cpu", "%idle", "_total", 10.0)
        self.assertEqual(1, mock_event.call_count)

        event_json = mock_event.call_args[0][0]
        self.assertIn(event.TELEMETRY_EVENT_PROVIDER_ID, event_json)
        self.assertIn("%idle", event_json)

        event_dictionary = json.loads(event_json)
        self.assertEqual(event_dictionary['providerId'], event.TELEMETRY_EVENT_PROVIDER_ID)

        for parameter in event_dictionary["parameters"]:
            if parameter['name'] == GuestAgentPerfCounterEventsSchema.Counter:
                self.assertEqual(parameter['value'], '%idle')
                break
        else:
            self.fail("Counter '%idle' not found in event parameters: {0}".format(repr(event_dictionary)))

    def test_cleanup_message(self):
        ev_logger = event.EventLogger()

        self.assertEqual(None, ev_logger._clean_up_message(None))
        self.assertEqual("", ev_logger._clean_up_message(""))
        self.assertEqual("Daemon Activate resource disk failure", ev_logger._clean_up_message(
            "Daemon Activate resource disk failure"))
        self.assertEqual("[M.A.E.CS-2.0.7] Target handler state", ev_logger._clean_up_message(
            '2019/10/07 21:54:16.629444 INFO [M.A.E.CS-2.0.7] Target handler state'))
        self.assertEqual("[M.A.E.CS-2.0.7] Initializing extension M.A.E.CS-2.0.7", ev_logger._clean_up_message(
            '2019/10/07 21:54:17.284385 INFO [M.A.E.CS-2.0.7] Initializing extension M.A.E.CS-2.0.7'))
        self.assertEqual("ExtHandler ProcessGoalState completed [incarnation 4; 4197 ms]", ev_logger._clean_up_message(
            "2019/10/07 21:55:38.474861 INFO ExtHandler ProcessGoalState completed [incarnation 4; 4197 ms]"))
        self.assertEqual("Daemon Azure Linux Agent Version:2.2.43", ev_logger._clean_up_message(
            "2019/10/07 21:52:28.615720 INFO Daemon Azure Linux Agent Version:2.2.43"))
        self.assertEqual('Daemon Cgroup controller "memory" is not mounted. Failed to create a cgroup for the VM Agent;'
                         ' resource usage will not be tracked',
                         ev_logger._clean_up_message('Daemon Cgroup controller "memory" is not mounted. Failed to '
                                                     'create a cgroup for the VM Agent; resource usage will not be '
                                                     'tracked'))
        self.assertEqual('ExtHandler Root directory /sys/fs/cgroup/memory/walinuxagent.extensions does not exist.',
                         ev_logger._clean_up_message("2019/10/08 23:45:05.691037 WARNING ExtHandler Root directory "
                                                     "/sys/fs/cgroup/memory/walinuxagent.extensions does not exist."))
        self.assertEqual("LinuxAzureDiagnostic started to handle.",
                         ev_logger._clean_up_message("2019/10/07 22:02:40 LinuxAzureDiagnostic started to handle."))
        self.assertEqual("VMAccess started to handle.",
                         ev_logger._clean_up_message("2019/10/07 21:56:58 VMAccess started to handle."))
        self.assertEqual(
            '[PERIODIC] ExtHandler Root directory /sys/fs/cgroup/memory/walinuxagent.extensions does not exist.',
            ev_logger._clean_up_message("2019/10/08 23:45:05.691037 WARNING [PERIODIC] ExtHandler Root directory "
                                        "/sys/fs/cgroup/memory/walinuxagent.extensions does not exist."))
        self.assertEqual("[PERIODIC] LinuxAzureDiagnostic started to handle.", ev_logger._clean_up_message(
            "2019/10/07 22:02:40 [PERIODIC] LinuxAzureDiagnostic started to handle."))
        self.assertEqual("[PERIODIC] VMAccess started to handle.",
                         ev_logger._clean_up_message("2019/10/07 21:56:58 [PERIODIC] VMAccess started to handle."))
        self.assertEqual('[PERIODIC] Daemon Cgroup controller "memory" is not mounted. Failed to create a cgroup for '
                         'the VM Agent; resource usage will not be tracked',
                         ev_logger._clean_up_message('[PERIODIC] Daemon Cgroup controller "memory" is not mounted. '
                                                     'Failed to create a cgroup for the VM Agent; resource usage will '
                                                     'not be tracked'))
        self.assertEqual('The time should be in UTC', ev_logger._clean_up_message(
            '2019-11-26T18:15:06.866746Z INFO The time should be in UTC'))
        self.assertEqual('The time should be in UTC', ev_logger._clean_up_message(
            '2019-11-26T18:15:06.866746Z The time should be in UTC'))
        self.assertEqual('[PERIODIC] The time should be in UTC', ev_logger._clean_up_message(
            '2019-11-26T18:15:06.866746Z INFO [PERIODIC] The time should be in UTC'))
        self.assertEqual('[PERIODIC] The time should be in UTC', ev_logger._clean_up_message(
            '2019-11-26T18:15:06.866746Z [PERIODIC] The time should be in UTC'))
