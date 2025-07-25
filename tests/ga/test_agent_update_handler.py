import contextlib
import json
import os
import random
import time

from azurelinuxagent.common import conf
from azurelinuxagent.common.event import WALAEventOperation
from azurelinuxagent.common.exception import AgentUpgradeExitException
from azurelinuxagent.common.future import ustr, httpclient
from azurelinuxagent.common.protocol.restapi import VMAgentUpdateStatuses

from azurelinuxagent.common.protocol.util import ProtocolUtil
from azurelinuxagent.common.version import CURRENT_VERSION, AGENT_NAME
from azurelinuxagent.ga.agent_update_handler import get_agent_update_handler
from azurelinuxagent.ga.guestagent import GuestAgent, INITIAL_UPDATE_STATE_FILE, RSM_UPDATE_STATE_FILE
from tests.ga.test_update import UpdateTestCase
from tests.lib.http_request_predicates import HttpRequestPredicates
from tests.lib.mock_wire_protocol import mock_wire_protocol, MockHttpResponse
from tests.lib.wire_protocol_data import DATA_FILE
from tests.lib.tools import clear_singleton_instances, load_bin_data, patch, skip_if_predicate_true


class TestAgentUpdate(UpdateTestCase):

    def setUp(self):
        UpdateTestCase.setUp(self)
        # Since ProtocolUtil is a singleton per thread, we need to clear it to ensure that the test cases do not
        # reuse a previous state
        clear_singleton_instances(ProtocolUtil)

    @contextlib.contextmanager
    def _get_agent_update_handler(self, test_data=None, autoupdate_frequency=0.001, autoupdate_enabled=True, initial_update_attempted=True, rsm_update_attempted=False, protocol_get_error=False, mock_get_header=None, mock_put_header=None, mock_random_update_time=True):
        # Default to DATA_FILE of test_data parameter raises the pylint warning
        # W0102: Dangerous default value DATA_FILE (builtins.dict) as argument (dangerous-default-value)
        test_data = DATA_FILE if test_data is None else test_data

        with mock_wire_protocol(test_data) as protocol:

            def get_handler(url, **kwargs):
                if HttpRequestPredicates.is_agent_package_request(url):
                    if not protocol_get_error:
                        agent_pkg = load_bin_data(self._get_agent_file_name(), self._agent_zip_dir)
                        return MockHttpResponse(status=httpclient.OK, body=agent_pkg)
                    else:
                        return MockHttpResponse(status=httpclient.SERVICE_UNAVAILABLE)

                return protocol.mock_wire_data.mock_http_get(url, **kwargs)

            def put_handler(url, *args, **_):
                if HttpRequestPredicates.is_host_plugin_status_request(url):
                    # Skip reading the HostGA request data as its encoded
                    return MockHttpResponse(status=500)
                protocol.aggregate_status = json.loads(args[0])
                return MockHttpResponse(status=201)

            http_get_handler = mock_get_header if mock_get_header else get_handler
            http_put_handler = mock_put_header if mock_put_header else put_handler

            protocol.set_http_handlers(http_get_handler=http_get_handler, http_put_handler=http_put_handler)

            if initial_update_attempted:
                open(os.path.join(conf.get_lib_dir(), INITIAL_UPDATE_STATE_FILE), "a").close()

            if rsm_update_attempted:
                open(os.path.join(conf.get_lib_dir(), RSM_UPDATE_STATE_FILE), "a").close()

            original_randint = random.randint

            def _mock_random_update_time(a, b):
                if mock_random_update_time:  # update should occur immediately
                    return 0
                if b == 1:  # handle tests where the normal or hotfix frequency is mocked to be very short (e.g., 1 second). Returning a very small delay (0.001 seconds) ensures the logic is tested without introducing significant waiting time
                    return 0.001
                return original_randint(a, b) + 10  # If none of the above conditions are met, the function returns additional 10-seconds delay. This might represent a normal delay for updates in scenarios where updates are not expected immediately

            with patch("azurelinuxagent.common.conf.get_autoupdate_enabled", return_value=autoupdate_enabled):
                with patch("azurelinuxagent.common.conf.get_autoupdate_frequency", return_value=autoupdate_frequency):
                    with patch("azurelinuxagent.ga.self_update_version_updater.random.randint", side_effect=_mock_random_update_time):
                        with patch("azurelinuxagent.common.conf.get_autoupdate_gafamily", return_value="Prod"):
                            with patch("azurelinuxagent.common.conf.get_enable_ga_versioning", return_value=True):
                                with patch("azurelinuxagent.common.event.EventLogger.add_event") as mock_telemetry:
                                    agent_update_handler = get_agent_update_handler(protocol)
                                    agent_update_handler._protocol = protocol
                                    yield agent_update_handler, mock_telemetry

    def _assert_agent_directories_available(self, versions):
        for version in versions:
            self.assertTrue(os.path.exists(self.agent_dir(version)), "Agent directory {0} not found".format(version))

    def _assert_agent_directories_exist_and_others_dont_exist(self, versions):
        self._assert_agent_directories_available(versions=versions)
        other_agents = [agent_dir for agent_dir in self.agent_dirs() if
                        agent_dir not in [self.agent_dir(version) for version in versions]]
        self.assertFalse(any(other_agents),
                         "All other agents should be purged from agent dir: {0}".format(other_agents))

    def _assert_agent_rsm_version_in_goal_state(self, mock_telemetry, inc=1, version="9.9.9.10"):
        upgrade_event_msgs = [kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                              'New agent version:{0} requested by RSM in Goal state incarnation_{1}'.format(version, inc) in kwarg['message'] and kwarg[
                                  'op'] == WALAEventOperation.AgentUpgrade]
        self.assertEqual(1, len(upgrade_event_msgs),
                         "Did not find the event indicating that the agent requested version found. Got: {0}".format(
                             mock_telemetry.call_args_list))

    def _assert_update_discovered_from_agent_manifest(self, mock_telemetry, inc=1, version="9.9.9.10"):
        upgrade_event_msgs = [kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                              'Self-update is ready to upgrade the new agent: {0} now before processing the goal state: incarnation_{1}'.format(version, inc) in kwarg['message'] and kwarg[
                                  'op'] == WALAEventOperation.AgentUpgrade]
        self.assertEqual(1, len(upgrade_event_msgs),
                         "Did not find the event indicating that the new version found. Got: {0}".format(
                             mock_telemetry.call_args_list))

    def _assert_no_agent_package_telemetry_emitted(self, mock_telemetry, version="9.9.9.10"):
        upgrade_event_msgs = [kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                              'No matching package found in the agent manifest for version: {0}'.format(version) in kwarg['message'] and kwarg[
                                  'op'] == WALAEventOperation.AgentUpgrade]
        self.assertEqual(1, len(upgrade_event_msgs),
                         "Did not find the event indicating that the agent package not found. Got: {0}".format(
                             mock_telemetry.call_args_list))

    def _assert_agent_exit_process_telemetry_emitted(self, message):
        self.assertIn("Current Agent {0} completed all update checks, exiting current process".format(CURRENT_VERSION), message)

    def test_it_should_not_update_when_autoupdate_disabled(self):
        self.prepare_agents(count=1)
        with self._get_agent_update_handler(autoupdate_enabled=False) as (agent_update_handler, mock_telemetry):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION)])
            self.assertEqual(0, len([kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                                     "requesting a new agent version" in kwarg['message'] and kwarg[
                                         'op'] == WALAEventOperation.AgentUpgrade]), "should not check for rsm version")

    def test_it_should_update_to_largest_version_if_ga_versioning_disabled(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"
        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            with patch.object(conf, "get_enable_ga_versioning", return_value=False):
                with self.assertRaises(AgentUpgradeExitException) as context:
                    agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_update_discovered_from_agent_manifest(mock_telemetry, version="99999.0.0.0")
            self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION), "99999.0.0.0"])
            self._assert_agent_exit_process_telemetry_emitted(ustr(context.exception.reason))

    def test_it_should_not_update_to_largest_version_if_manifest_download_time_not_elapsed(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file["ga_manifest"] = "wire/ga_manifest_no_uris.xml"
        with self._get_agent_update_handler(test_data=data_file, autoupdate_frequency=10) as (agent_update_handler, _):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self.assertFalse(os.path.exists(self.agent_dir("99999.0.0.0")),
                             "New agent directory should not be found")
            agent_update_handler._protocol.mock_wire_data.set_ga_manifest("wire/ga_manifest.xml")
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self.assertFalse(os.path.exists(self.agent_dir("99999.0.0.0")),
                             "New agent directory should not be found")

    def test_it_should_not_do_self_update_if_update_time_is_not_elapsed(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file["ga_manifest"] = "wire/ga_manifest_no_uris.xml"
        with self._get_agent_update_handler(test_data=data_file, mock_random_update_time=False) as (agent_update_handler, _):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self.assertFalse(os.path.exists(self.agent_dir("99999.0.0.0")),
                             "New agent directory should not be found")
            agent_update_handler._protocol.mock_wire_data.set_ga_manifest("wire/ga_manifest.xml")
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self.assertFalse(os.path.exists(self.agent_dir("99999.0.0.0")),
                             "New agent directory should not be found")

    def test_it_should_update_to_largest_version_after_time_window_elapsed(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file["ga_manifest"] = "wire/ga_manifest_no_uris.xml"
        with patch("azurelinuxagent.common.conf.get_self_update_hotfix_frequency", return_value=1):
            with patch("azurelinuxagent.common.conf.get_self_update_regular_frequency", return_value=1):
                with self._get_agent_update_handler(test_data=data_file, mock_random_update_time=False) as (agent_update_handler, mock_telemetry):
                    with self.assertRaises(AgentUpgradeExitException) as context:
                        agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
                        self.assertFalse(os.path.exists(self.agent_dir("99999.0.0.0")),
                                         "New agent directory should not be found")
                        agent_update_handler._protocol.mock_wire_data.set_ga_manifest("wire/ga_manifest.xml")
                        agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
                        agent_update_handler._protocol.client.update_goal_state()
                        # sleeping for update window to elapse
                        time.sleep(0.1)
                        agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
                    self._assert_update_discovered_from_agent_manifest(mock_telemetry, inc=2, version="99999.0.0.0")
                    self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION), "99999.0.0.0"])
                    self._assert_agent_exit_process_telemetry_emitted(ustr(context.exception.reason))

    def test_it_should_not_allow_update_if_largest_version_below_current_version(self):
        self.prepare_agents(count=1)
        data_file = DATA_FILE.copy()
        data_file["ga_manifest"] = "wire/ga_manifest_no_upgrade.xml"
        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, _):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION)])

    def test_it_should_update_to_largest_version_if_rsm_version_not_available(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf.xml"
        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            with self.assertRaises(AgentUpgradeExitException) as context:
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_update_discovered_from_agent_manifest(mock_telemetry, version="99999.0.0.0")
            self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION), "99999.0.0.0"])
            self._assert_agent_exit_process_telemetry_emitted(ustr(context.exception.reason))

    def test_it_should_not_download_manifest_again_if_last_attempted_download_time_not_elapsed(self):
        self.prepare_agents(count=1)
        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf.xml"
        with self._get_agent_update_handler(test_data=data_file, autoupdate_frequency=10, protocol_get_error=True) as (agent_update_handler, _):
            # making multiple agent update attempts
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            mock_wire_data = agent_update_handler._protocol.mock_wire_data
            self.assertEqual(1, mock_wire_data.call_counts['manifest_of_ga.xml'], "Agent manifest should not be downloaded again")

    def test_it_should_download_manifest_if_last_attempted_download_time_is_elapsed(self):
        self.prepare_agents(count=1)
        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf.xml"

        with self._get_agent_update_handler(test_data=data_file, autoupdate_frequency=0.00001, protocol_get_error=True) as (agent_update_handler, _):
            # making multiple agent update attempts
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

        mock_wire_data = agent_update_handler._protocol.mock_wire_data
        self.assertEqual(3, mock_wire_data.call_counts['manifest_of_ga.xml'], "Agent manifest should be downloaded in all attempts")

    def test_it_should_not_agent_update_if_rsm_version_is_same_as_current_version(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        # Set the test environment by adding 20 random agents to the agent directory
        self.prepare_agents()
        self.assertEqual(20, self.agent_count(), "Agent directories not set properly")

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(
                str(CURRENT_VERSION))
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self.assertEqual(0, len([kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                                     "requesting a new agent version" in kwarg['message'] and kwarg[
                                         'op'] == WALAEventOperation.AgentUpgrade]), "rsm version should be same as current version")
            self.assertFalse(os.path.exists(self.agent_dir("99999.0.0.0")),
                             "New agent directory should not be found")

    def test_it_should_upgrade_agent_if_rsm_version_is_available_greater_than_current_version(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        # Set the test environment by adding 20 random agents to the agent directory
        self.prepare_agents()
        self.assertEqual(20, self.agent_count(), "Agent directories not set properly")

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            with self.assertRaises(AgentUpgradeExitException) as context:
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_agent_rsm_version_in_goal_state(mock_telemetry, version="9.9.9.10")
            self._assert_agent_directories_exist_and_others_dont_exist(versions=["9.9.9.10", str(CURRENT_VERSION)])
            self._assert_agent_exit_process_telemetry_emitted(ustr(context.exception.reason))

    def test_it_should_not_allow_rsm_downgrade_if_rsm_version_is_available_less_than_current_version(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        # Set the test environment by adding 20 random agents to the agent directory
        self.prepare_agents()
        self.assertEqual(20, self.agent_count(), "Agent directories not set properly")

        downgraded_version = "2.5.0"

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, _):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(downgraded_version)
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self.assertFalse(os.path.exists(self.agent_dir(downgraded_version)),"New agent directory should not be found")

    @skip_if_predicate_true(lambda: True, "Enable this test when rsm downgrade scenario fixed")
    def test_it_should_downgrade_agent_if_rsm_version_is_available_less_than_current_version(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        # Set the test environment by adding 20 random agents to the agent directory
        self.prepare_agents()
        self.assertEqual(20, self.agent_count(), "Agent directories not set properly")

        downgraded_version = "2.5.0"

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(downgraded_version)
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            with self.assertRaises(AgentUpgradeExitException) as context:
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_agent_rsm_version_in_goal_state(mock_telemetry, inc=2, version=downgraded_version)
            self._assert_agent_directories_exist_and_others_dont_exist(
                versions=[downgraded_version, str(CURRENT_VERSION)])
            self._assert_agent_exit_process_telemetry_emitted(ustr(context.exception.reason))

    def test_it_should_not_do_rsm_update_if_gs_not_updated_in_next_attempt(self):
        self.prepare_agents(count=1)
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"
        version = "9.9.9.999"
        with self._get_agent_update_handler(test_data=data_file, autoupdate_frequency=10) as (agent_update_handler, mock_telemetry):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(version)
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            self._assert_agent_rsm_version_in_goal_state(mock_telemetry, inc=2, version=version)
            self._assert_no_agent_package_telemetry_emitted(mock_telemetry, version=version)
            # Now we shouldn't check for download if update not allowed(GS not updated).This run should not add new logs
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), False)
            self._assert_agent_rsm_version_in_goal_state(mock_telemetry, inc=2, version=version)
            self._assert_no_agent_package_telemetry_emitted(mock_telemetry, version=version)

    def test_it_should_not_downgrade_below_daemon_version(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        # Set the test environment by adding 20 random agents to the agent directory
        self.prepare_agents()
        self.assertEqual(20, self.agent_count(), "Agent directories not set properly")

        downgraded_version = "1.2.0"

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, _):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(downgraded_version)
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self.assertFalse(os.path.exists(self.agent_dir(downgraded_version)),
                             "New agent directory should not be found")

    def test_it_should_update_to_largest_version_if_vm_not_enabled_for_rsm_upgrades(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf_vm_not_enabled_for_rsm_upgrades.xml"
        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            with self.assertRaises(AgentUpgradeExitException) as context:
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_update_discovered_from_agent_manifest(mock_telemetry, version="99999.0.0.0")
            self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION), "99999.0.0.0"])
            self._assert_agent_exit_process_telemetry_emitted(ustr(context.exception.reason))

    def test_it_should_not_update_to_version_if_version_not_from_rsm(self):
        self.prepare_agents(count=1)
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_version_not_from_rsm.xml"
        downgraded_version = "2.5.0"

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, _):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(downgraded_version)
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_agent_directories_exist_and_others_dont_exist(
                versions=[str(CURRENT_VERSION)])
            self.assertFalse(os.path.exists(self.agent_dir(downgraded_version)),
                             "New agent directory should not be found")

    def test_handles_if_rsm_version_not_found_in_pkgs_to_download(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        # Set the test environment by adding 20 random agents to the agent directory
        self.prepare_agents()
        self.assertEqual(20, self.agent_count(), "Agent directories not set properly")

        version = "9.9.9.999"

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(version)
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            self._assert_agent_rsm_version_in_goal_state(mock_telemetry, inc=2, version=version)
            self.assertFalse(os.path.exists(self.agent_dir(version)),
                             "New agent directory should not be found")

            self._assert_no_agent_package_telemetry_emitted(mock_telemetry, version=version)

    def test_handles_missing_agent_family(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_missing_family.xml"

        # Set the test environment by adding 20 random agents to the agent directory
        self.prepare_agents()
        self.assertEqual(20, self.agent_count(), "Agent directories not set properly")

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            self.assertFalse(os.path.exists(self.agent_dir("99999.0.0.0")),
                             "New agent directory should not be found")

            self.assertEqual(1, len([kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                                     "No manifest links found for agent family" in kwarg[
                                         'message'] and kwarg[
                                         'op'] == WALAEventOperation.AgentUpgrade]), "Agent manifest should not be in GS")

            # making multiple agent update attempts and assert only one time logged
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), False)
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), False)

            self.assertEqual(1, len([kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                                     "No manifest links found for agent family" in kwarg[
                                         'message'] and kwarg[
                                         'op'] == WALAEventOperation.AgentUpgrade]),
                             "Agent manifest error should be logged once if it's same goal state")

    def test_it_should_report_update_status_with_success(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, _):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(
                str(CURRENT_VERSION))
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            vm_agent_update_status = agent_update_handler.get_vmagent_update_status()
            self.assertEqual(VMAgentUpdateStatuses.Success, vm_agent_update_status.status)
            self.assertEqual(0, vm_agent_update_status.code)
            self.assertEqual(str(CURRENT_VERSION), vm_agent_update_status.expected_version)

    def test_it_should_not_report_update_status_when_self_update_used(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf.xml"
        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, _):
            with self.assertRaises(AgentUpgradeExitException):
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
        vm_agent_update_status = agent_update_handler.get_vmagent_update_status()
        self.assertIsNone(vm_agent_update_status, "VM Agent Update Status should not be set when self-update is used")

    def test_it_should_report_update_with_error_if_auto_update_is_disabled_and_rsm_update_used(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        with self._get_agent_update_handler(test_data=data_file, rsm_update_attempted=True) as (agent_update_handler, _):
            with patch("azurelinuxagent.common.conf.get_auto_update_to_latest_version", return_value=False):
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
                vm_agent_update_status = agent_update_handler.get_vmagent_update_status()
                self.assertEqual(1, vm_agent_update_status.code)
                self.assertEqual(VMAgentUpdateStatuses.Error, vm_agent_update_status.status)
                self.assertIn("Auto update is disabled, skipping agent update", vm_agent_update_status.message)

    def test_it_should_report_update_status_with_error_on_download_fail(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        with self._get_agent_update_handler(test_data=data_file, protocol_get_error=True) as (agent_update_handler, _):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            vm_agent_update_status = agent_update_handler.get_vmagent_update_status()
            self.assertEqual(VMAgentUpdateStatuses.Error, vm_agent_update_status.status)
            self.assertEqual(1, vm_agent_update_status.code)
            self.assertEqual("9.9.9.10", vm_agent_update_status.expected_version)
            self.assertIn("Failed to download WALinuxAgent-9.9.9.10 from all URIs", vm_agent_update_status.message)

    def test_it_should_not_report_error_status_if_new_rsm_version_is_same_as_current_after_last_update_attempt_failed(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        with self._get_agent_update_handler(test_data=data_file, protocol_get_error=True) as (agent_update_handler, _):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            vm_agent_update_status = agent_update_handler.get_vmagent_update_status()
            self.assertEqual(VMAgentUpdateStatuses.Error, vm_agent_update_status.status)
            self.assertEqual(1, vm_agent_update_status.code)
            self.assertEqual("9.9.9.10", vm_agent_update_status.expected_version)
            self.assertIn("Failed to download WALinuxAgent-9.9.9.10 from all URIs", vm_agent_update_status.message)

            # Send same version GS after last update attempt failed
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(
                str(CURRENT_VERSION))
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            vm_agent_update_status = agent_update_handler.get_vmagent_update_status()
            self.assertEqual(VMAgentUpdateStatuses.Success, vm_agent_update_status.status)
            self.assertEqual(0, vm_agent_update_status.code)
            self.assertEqual(str(CURRENT_VERSION), vm_agent_update_status.expected_version)

    def test_it_should_report_update_status_with_missing_rsm_version_error(self):
        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf_version_missing_in_agent_family.xml"

        with self._get_agent_update_handler(test_data=data_file, protocol_get_error=True, rsm_update_attempted=True) as (agent_update_handler, _):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            vm_agent_update_status = agent_update_handler.get_vmagent_update_status()
            self.assertEqual(VMAgentUpdateStatuses.Error, vm_agent_update_status.status)
            self.assertEqual(1, vm_agent_update_status.code)
            self.assertIn("missing version property. So, skipping agent update", vm_agent_update_status.message)

    def test_it_should_not_log_same_error_next_hours(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_missing_family.xml"

        # Set the test environment by adding 20 random agents to the agent directory
        self.prepare_agents()
        self.assertEqual(20, self.agent_count(), "Agent directories not set properly")

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            self.assertFalse(os.path.exists(self.agent_dir("99999.0.0.0")),
                             "New agent directory should not be found")

        self.assertEqual(1, len([kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                                 "No manifest links found for agent family" in kwarg[
                                     'message'] and kwarg[
                                     'op'] == WALAEventOperation.AgentUpgrade]), "Agent manifest should not be in GS")

        agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

        self.assertEqual(1, len([kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                                 "No manifest links found for agent family" in kwarg[
                                     'message'] and kwarg[
                                     'op'] == WALAEventOperation.AgentUpgrade]), "Agent manifest should not be in GS")

    def test_it_should_save_rsm_state_of_the_most_recent_goal_state(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, _):
            with self.assertRaises(AgentUpgradeExitException):
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            state_file = os.path.join(conf.get_lib_dir(), RSM_UPDATE_STATE_FILE)
            self.assertTrue(os.path.exists(state_file), "The rsm state file was not saved (can't find {0})".format(state_file))

            # check if state gets updated if most recent goal state has different values
            agent_update_handler._protocol.mock_wire_data.set_extension_config_is_vm_enabled_for_rsm_upgrades("False")
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            with self.assertRaises(AgentUpgradeExitException):
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            self.assertFalse(os.path.exists(state_file), "The rsm file should be removed (file: {0})".format(state_file))

    def test_it_should_not_update_to_latest_if_flag_is_disabled(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf.xml"
        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, _):
            with patch("azurelinuxagent.common.conf.get_auto_update_to_latest_version", return_value=False):
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION)])

    def test_it_should_continue_with_update_if_number_of_update_attempts_less_than_3(self):
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"

        latest_version = self.prepare_agents(count=2)
        self.expand_agents()
        latest_path = os.path.join(self.tmp_dir, "{0}-{1}".format(AGENT_NAME, latest_version))
        agent = GuestAgent.from_installed_agent(latest_path)
        # marking agent as bad agent on first attempt
        agent.mark_failure(is_fatal=True)
        agent.inc_update_attempt_count()
        self.assertTrue(agent.is_blacklisted, "Agent should be blacklisted")
        self.assertEqual(1, agent.get_update_attempt_count(), "Agent update attempts should be 1")
        with self._get_agent_update_handler(test_data=data_file) as (agent_update_handler, mock_telemetry):
            # Rest 2 attempts it should continue with update even agent is marked as bad agent in first attempt
            for i in range(2):
                with self.assertRaises(AgentUpgradeExitException):
                    agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family(
                        str(latest_version))
                    agent_update_handler._protocol.mock_wire_data.set_version_in_ga_manifest(str(latest_version))
                    agent_update_handler._protocol.mock_wire_data.set_incarnation(i+2)
                    agent_update_handler._protocol.client.update_goal_state()
                    agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
                self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION), str(latest_version)])
                agent = GuestAgent.from_installed_agent(latest_path)
                self.assertFalse(agent.is_blacklisted, "Agent should not be blacklisted")
                self.assertEqual(i+2, agent.get_update_attempt_count(), "Agent update attempts should be {0}".format(i+2))

            # check if next update is not attempted
            agent.mark_failure(is_fatal=True)
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            agent = GuestAgent.from_installed_agent(latest_path)
            self.assertTrue(agent.is_blacklisted, "Agent should be blacklisted")
            self.assertEqual(3, agent.get_update_attempt_count(), "Agent update attempts should be 3")
            self.assertEqual(1, len([kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                                     "Attempted enough update retries for version: {0} but still agent not recovered from bad state".format(latest_version) in kwarg[
                                         'message'] and kwarg[
                                         'op'] == WALAEventOperation.AgentUpgrade]),
                             "Update is not allowed after 3 attempts")

    def test_it_should_fail_the_update_if_agent_pkg_is_invalid(self):
        agent_uri = 'https://foo.blob.core.windows.net/bar/OSTCExtensions.WALinuxAgent__9.9.9.10'

        def http_get_handler(uri, *_, **__):
            if uri in (agent_uri, 'http://168.63.129.16:32526/extensionArtifact'):
                response = load_bin_data("ga/WALinuxAgent-9.9.9.10-no_manifest.zip")
                return MockHttpResponse(status=httpclient.OK, body=response)
            return None
        self.prepare_agents(count=1)
        data_file = DATA_FILE.copy()
        data_file["ext_conf"] = "wire/ext_conf_rsm_version.xml"
        with self._get_agent_update_handler(test_data=data_file, mock_get_header=http_get_handler) as (agent_update_handler, mock_telemetry):
            agent_update_handler._protocol.mock_wire_data.set_version_in_agent_family("9.9.9.10")
            agent_update_handler._protocol.mock_wire_data.set_incarnation(2)
            agent_update_handler._protocol.client.update_goal_state()
            agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION)])
            self.assertEqual(1, len([kwarg['message'] for _, kwarg in mock_telemetry.call_args_list if
                                     "Downloaded agent package: WALinuxAgent-9.9.9.10 is missing agent handler manifest file" in kwarg['message'] and kwarg[
                                         'op'] == WALAEventOperation.AgentUpgrade]), "Agent update should fail")

    def test_it_should_use_self_update_for_first_update_always(self):
        self.prepare_agents(count=1)

        # mock the goal state as vm enrolled into RSM
        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf_rsm_version.xml"
        with self._get_agent_update_handler(test_data=data_file, initial_update_attempted=False) as (agent_update_handler, mock_telemetry):
            with self.assertRaises(AgentUpgradeExitException) as context:
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)
            # Verifying agent used self-update for initial update
            self._assert_update_discovered_from_agent_manifest(mock_telemetry, version="99999.0.0.0")
            self._assert_agent_directories_exist_and_others_dont_exist(versions=[str(CURRENT_VERSION), "99999.0.0.0"])
            self._assert_agent_exit_process_telemetry_emitted(ustr(context.exception.reason))

        state_file = os.path.join(conf.get_lib_dir(), INITIAL_UPDATE_STATE_FILE)
        self.assertTrue(os.path.exists(state_file),
                        "The first update state file was not saved (can't find {0})".format(state_file))

    def test_it_should_honor_any_update_type_after_first_update(self):
        self.prepare_agents(count=1)

        data_file = DATA_FILE.copy()
        data_file['ext_conf'] = "wire/ext_conf_rsm_version.xml"
        # mocking initial update attempt as true
        with self._get_agent_update_handler(test_data=data_file, initial_update_attempted=True) as (agent_update_handler, mock_telemetry):
            with self.assertRaises(AgentUpgradeExitException) as context:
                agent_update_handler.run(agent_update_handler._protocol.get_goal_state(), True)

            # Verifying agent honored RSM update
            self._assert_agent_rsm_version_in_goal_state(mock_telemetry, version="9.9.9.10")
            self._assert_agent_directories_exist_and_others_dont_exist(versions=["9.9.9.10", str(CURRENT_VERSION)])
            self._assert_agent_exit_process_telemetry_emitted(ustr(context.exception.reason))
