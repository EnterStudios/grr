#!/usr/bin/env python
"""Test the flow_management interface."""


import os


import mock

from grr.gui import api_call_handler_utils
from grr.gui import api_call_router_with_approval_checks
from grr.gui import gui_test_lib
from grr.gui import runtests_test
from grr.gui.api_plugins import flow as api_flow

from grr.lib import action_mocks
from grr.lib import aff4
from grr.lib import flags
from grr.lib import flow
from grr.lib import hunts
from grr.lib import output_plugin
from grr.lib import test_lib
from grr.lib import utils
from grr.lib.flows.general import filesystem as flows_filesystem
from grr.lib.flows.general import processes as flows_processes
from grr.lib.flows.general import transfer as flows_transfer
from grr.lib.flows.general import webhistory as flows_webhistory
from grr.lib.hunts import standard
from grr.lib.hunts import standard_test
from grr.lib.output_plugins import email_plugin
from grr.lib.rdfvalues import client as rdf_client
from grr.lib.rdfvalues import crypto as rdf_crypto
from grr.lib.rdfvalues import flows as rdf_flows
from grr.lib.rdfvalues import paths as rdf_paths
from grr.lib.rdfvalues import structs as rdf_structs
from grr.proto import tests_pb2


class RecursiveTestFlowArgs(rdf_structs.RDFProtoStruct):
  protobuf = tests_pb2.RecursiveTestFlowArgs


class RecursiveTestFlow(flow.GRRFlow):
  """A test flow which starts some subflows."""
  args_type = RecursiveTestFlowArgs

  # If a flow doesn't have a category, it can't be started/terminated by a
  # non-supervisor user when FullAccessControlManager is used.
  category = "/Test/"

  @flow.StateHandler()
  def Start(self):
    if self.args.depth < 2:
      for i in range(2):
        self.Log("Subflow call %d", i)
        self.CallFlow(
            RecursiveTestFlow.__name__,
            depth=self.args.depth + 1,
            next_state="End")


class FlowWithOneLogStatement(flow.GRRFlow):
  """Flow that logs a single statement."""

  @flow.StateHandler()
  def Start(self):
    self.Log("I do log.")


class FlowWithOneStatEntryResult(flow.GRRFlow):
  """Test flow that calls SendReply once with a StatEntry value."""

  @flow.StateHandler()
  def Start(self):
    self.SendReply(rdf_client.StatEntry(aff4path="aff4:/some/unique/path"))


class FlowWithOneNetworkConnectionResult(flow.GRRFlow):
  """Test flow that calls SendReply once with a NetworkConnection value."""

  @flow.StateHandler()
  def Start(self):
    self.SendReply(rdf_client.NetworkConnection(pid=42))


class FlowWithOneHashEntryResult(flow.GRRFlow):
  """Test flow that calls SendReply once with a HashEntry value."""

  @flow.StateHandler()
  def Start(self):
    hash_result = rdf_crypto.Hash(
        sha256=("9e8dc93e150021bb4752029ebbff51394aa36f069cf19901578"
                "e4f06017acdb5").decode("hex"),
        sha1="6dd6bee591dfcb6d75eb705405302c3eab65e21a".decode("hex"),
        md5="8b0a15eefe63fd41f8dc9dee01c5cf9a".decode("hex"))
    self.SendReply(hash_result)


class TestFlowManagement(gui_test_lib.GRRSeleniumTest,
                         standard_test.StandardHuntTestMixin):
  """Test the flow management GUI."""

  def setUp(self):
    super(TestFlowManagement, self).setUp()

    with self.ACLChecksDisabled():
      self.client_id = rdf_client.ClientURN("C.0000000000000001")
      with aff4.FACTORY.Open(
          self.client_id, mode="rw", token=self.token) as client:
        client.Set(client.Schema.HOSTNAME("HostC.0000000000000001"))
      self.RequestAndGrantClientApproval(self.client_id)
      self.action_mock = action_mocks.FileFinderClientMock()

  def testOpeningManageFlowsOfUnapprovedClientRedirectsToHostInfoPage(self):
    self.Open("/#/clients/C.0000000000000002/flows/")

    # As we don't have an approval for C.0000000000000002, we should be
    # redirected to the host info page.
    self.WaitUntilEqual("/#/clients/C.0000000000000002/host-info",
                        self.GetCurrentUrlPath)
    self.WaitUntil(self.IsTextPresent,
                   "You do not have an approval for this client.")

  def testPageTitleReflectsSelectedFlow(self):
    pathspec = rdf_paths.PathSpec(
        path=os.path.join(self.base_path, "test.plist"),
        pathtype=rdf_paths.PathSpec.PathType.OS)
    flow_urn = flow.GRRFlow.StartFlow(
        flow_name=flows_transfer.GetFile.__name__,
        client_id=self.client_id,
        pathspec=pathspec,
        token=self.token)

    self.Open("/#/clients/C.0000000000000001/flows/")
    self.WaitUntilEqual("GRR | C.0000000000000001 | Flows", self.GetPageTitle)

    self.Click("css=td:contains('GetFile')")
    self.WaitUntilEqual("GRR | C.0000000000000001 | " + flow_urn.Basename(),
                        self.GetPageTitle)

  def testFlowManagement(self):
    """Test that scheduling flows works."""
    self.Open("/")

    self.Type("client_query", "C.0000000000000001")
    self.Click("client_query_submit")

    self.WaitUntilEqual(u"C.0000000000000001", self.GetText,
                        "css=span[type=subject]")

    # Choose client 1
    self.Click("css=td:contains('0001')")

    # First screen should be the Host Information already.
    self.WaitUntil(self.IsTextPresent, "HostC.0000000000000001")

    self.Click("css=a[grrtarget='client.launchFlows']")
    self.Click("css=#_Processes")
    self.Click("link=" + flows_processes.ListProcesses.__name__)
    self.WaitUntil(self.IsTextPresent, "C.0000000000000001")

    self.WaitUntil(self.IsTextPresent, "List running processes on a system.")

    self.Click("css=button.Launch")
    self.WaitUntil(self.IsTextPresent, "Launched Flow ListProcesses")

    self.Click("css=#_Browser")
    # Wait until the tree has expanded.
    self.WaitUntil(self.IsTextPresent, flows_webhistory.FirefoxHistory.__name__)

    # Check that we can get a file in chinese
    self.Click("css=#_Filesystem")

    # Wait until the tree has expanded.
    self.WaitUntil(self.IsTextPresent,
                   flows_filesystem.UpdateSparseImageChunks.__name__)

    self.Click("link=" + flows_transfer.GetFile.__name__)

    self.Select("css=.form-group:has(> label:contains('Pathtype')) select",
                "OS")
    self.Type("css=.form-group:has(> label:contains('Path')) input",
              u"/dev/c/msn[1].exe")

    self.Click("css=button.Launch")

    self.WaitUntil(self.IsTextPresent, "Launched Flow GetFile")

    # Test that recursive tests are shown in a tree table.
    with self.ACLChecksDisabled():
      flow.GRRFlow.StartFlow(
          client_id="aff4:/C.0000000000000001",
          flow_name=RecursiveTestFlow.__name__,
          token=self.token)

    self.Click("css=a[grrtarget='client.flows']")

    # Some rows are present in the DOM but hidden because parent flow row
    # wasn't expanded yet. Due to this, we have to explicitly filter rows
    # with "visible" jQuery filter.
    self.WaitUntilEqual("RecursiveTestFlow", self.GetText,
                        "css=grr-client-flows-list tr:visible:nth(1) td:nth(2)")

    self.WaitUntilEqual("GetFile", self.GetText,
                        "css=grr-client-flows-list tr:visible:nth(2) td:nth(2)")

    # Click on the first tree_closed to open it.
    self.Click("css=grr-client-flows-list tr:visible:nth(1) .tree_closed")

    self.WaitUntilEqual("RecursiveTestFlow", self.GetText,
                        "css=grr-client-flows-list tr:visible:nth(2) td:nth(2)")

    # Select the requests tab
    self.Click("css=td:contains(GetFile)")
    self.Click("css=li[heading=Requests]")

    self.WaitUntil(self.IsElementPresent,
                   "css=td:contains(flow:request:00000001)")

    # Check that a StatFile client action was issued as part of the GetFile
    # flow.
    self.WaitUntil(self.IsElementPresent,
                   "css=.tab-content td.proto_value:contains(StatFile)")

  def testOverviewIsShownForNestedFlows(self):
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          RecursiveTestFlow.__name__,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")

    # There should be a RecursiveTestFlow in the list. Expand nested flows.
    self.Click("css=tr:contains('RecursiveTestFlow') span.tree_branch")
    # Click on a nested flow.
    self.Click("css=tr:contains('RecursiveTestFlow'):nth(2)")

    # Nested flow should have Depth argument set to 1.
    self.WaitUntil(self.IsElementPresent,
                   "css=td:contains('Depth') ~ td:nth(0):contains('1')")

    # Check that flow id of this flow has forward slash - i.e. consists of
    # 2 components.
    self.WaitUntil(self.IsTextPresent, "Flow ID")
    flow_id = self.GetText("css=dt:contains('Flow ID') ~ dd:nth(0)")
    self.assertTrue("/" in flow_id)

  def testOverviewIsShownForNestedHuntFlows(self):
    with self.ACLChecksDisabled():
      with hunts.GRRHunt.StartHunt(
          hunt_name=standard.GenericHunt.__name__,
          flow_runner_args=rdf_flows.FlowRunnerArgs(
              flow_name=RecursiveTestFlow.__name__),
          client_rate=0,
          token=self.token) as hunt:
        hunt.Run()

      self.AssignTasksToClients(client_ids=[self.client_id])
      self.RunHunt(client_ids=[self.client_id])

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")

    # There should be a RecursiveTestFlow in the list. Expand nested flows.
    self.Click("css=tr:contains('RecursiveTestFlow') span.tree_branch")
    # Click on a nested flow.
    self.Click("css=tr:contains('RecursiveTestFlow'):nth(2)")

    # Nested flow should have Depth argument set to 1.
    self.WaitUntil(self.IsElementPresent,
                   "css=td:contains('Depth') ~ td:nth(0):contains('1')")

    # Check that flow id of this flow has forward slash - i.e. consists of
    # 2 components.
    self.WaitUntil(self.IsTextPresent, "Flow ID")
    flow_id = self.GetText("css=dt:contains('Flow ID') ~ dd:nth(0)")
    self.assertTrue("/" in flow_id)

  def testNotificationPointingToFlowIsShownOnFlowCompletion(self):
    self.Open("/")

    pathspec = rdf_paths.PathSpec(
        path=os.path.join(self.base_path, "test.plist"),
        pathtype=rdf_paths.PathSpec.PathType.OS)
    flow_urn = flow.GRRFlow.StartFlow(
        flow_name=flows_transfer.GetFile.__name__,
        client_id=self.client_id,
        pathspec=pathspec,
        token=self.token)

    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          flow_urn,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    # Clicking on this should show the notifications table.
    self.Click("css=button[id=notification_button]")
    self.WaitUntil(self.IsTextPresent, "Notifications")

    # Click on the "flow completed" notification.
    self.Click("css=td:contains('Flow GetFile completed')")
    self.WaitUntilNot(self.IsTextPresent, "Notifications")

    # Check that clicking on a notification changes the location and shows
    # the flow page.
    self.WaitUntilEqual("/#/clients/%s/flows/%s" % (self.client_id.Basename(),
                                                    flow_urn.Basename()),
                        self.GetCurrentUrlPath)
    self.WaitUntil(self.IsTextPresent, utils.SmartStr(flow_urn))

  def testLogsCanBeOpenedByClickingOnLogsTab(self):
    # RecursiveTestFlow doesn't send any results back.
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          "FlowWithOneLogStatement",
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('FlowWithOneLogStatement')")
    self.Click("css=li[heading=Log]")

    self.WaitUntil(self.IsTextPresent, "I do log.")

  def testLogTimestampsArePresentedInUTC(self):
    with self.ACLChecksDisabled():
      with test_lib.FakeTime(42):
        for _ in test_lib.TestFlowHelper(
            "FlowWithOneLogStatement",
            self.action_mock,
            client_id=self.client_id,
            token=self.token):
          pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('FlowWithOneLogStatement')")
    self.Click("css=li[heading=Log]")

    self.WaitUntil(self.IsTextPresent, "1970-01-01 00:00:42 UTC")

  def testResultsAreDisplayedInResultsTab(self):
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          "FlowWithOneStatEntryResult",
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('FlowWithOneStatEntryResult')")
    self.Click("css=li[heading=Results]")

    self.WaitUntil(self.IsTextPresent, "aff4:/some/unique/path")

  def testEmptyTableIsDisplayedInResultsWhenNoResults(self):
    with self.ACLChecksDisabled():
      flow.GRRFlow.StartFlow(
          flow_name="FlowWithOneStatEntryResult",
          client_id=self.client_id,
          sync=False,
          token=self.token)

    self.Open("/#c=" + self.client_id.Basename())
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('FlowWithOneStatEntryResult')")
    self.Click("css=li[heading=Results]")

    self.WaitUntil(self.IsElementPresent, "css=#main_bottomPane table thead "
                   "th:contains('Value')")

  def testExportTabIsEnabledForStatEntryResults(self):
    with self.ACLChecksDisabled():
      for s in test_lib.TestFlowHelper(
          "FlowWithOneStatEntryResult",
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        session_id = s

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('FlowWithOneStatEntryResult')")
    self.Click("css=li[heading=Results]")
    self.Click("link=Show GRR export tool command")

    self.WaitUntil(self.IsTextPresent, "--username %s collection_files "
                   "--path %s/Results" % (self.token.username, session_id))

  def testHashesAreDisplayedCorrectly(self):
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          "FlowWithOneHashEntryResult",
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('FlowWithOneHashEntryResult')")
    self.Click("css=li[heading=Results]")

    self.WaitUntil(self.IsTextPresent,
                   "9e8dc93e150021bb4752029ebbff51394aa36f069cf19901578"
                   "e4f06017acdb5")
    self.WaitUntil(self.IsTextPresent,
                   "6dd6bee591dfcb6d75eb705405302c3eab65e21a")
    self.WaitUntil(self.IsTextPresent, "8b0a15eefe63fd41f8dc9dee01c5cf9a")

  def testExportCommandIsNotDisabledWhenNoResults(self):
    # RecursiveTestFlow doesn't send any results back.
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          RecursiveTestFlow.__name__,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('RecursiveTestFlow')")
    self.Click("css=li[heading=Results]")
    self.WaitUntil(self.IsElementPresent,
                   "css=grr-flow-results:contains('Value')")
    self.WaitUntilNot(self.IsTextPresent, "Show GRR export tool command")

  def testExportCommandIsNotShownForNonFileResults(self):
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          "FlowWithOneNetworkConnectionResult",
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('FlowWithOneNetworkConnectionResult')")
    self.Click("css=li[heading=Results]")
    self.WaitUntil(self.IsElementPresent,
                   "css=grr-flow-results:contains('Value')")
    self.WaitUntilNot(self.IsTextPresent, "Show GRR export tool command")

  def testCancelFlowWorksCorrectly(self):
    """Tests that cancelling flows works."""
    flow.GRRFlow.StartFlow(
        client_id=self.client_id,
        flow_name=RecursiveTestFlow.__name__,
        token=self.token)

    # Open client and find the flow
    self.Open("/")

    self.Type("client_query", "C.0000000000000001")
    self.Click("client_query_submit")

    self.WaitUntilEqual(u"C.0000000000000001", self.GetText,
                        "css=span[type=subject]")
    self.Click("css=td:contains('0001')")
    self.Click("css=a[grrtarget='client.flows']")

    self.Click("css=td:contains('RecursiveTestFlow')")
    self.Click("css=button[name=cancel_flow]")

    # The window should be updated now
    self.WaitUntil(self.IsTextPresent, "Cancelled in GUI")

  def testGlobalFlowManagement(self):
    """Test that scheduling flows works."""
    with self.ACLChecksDisabled():
      self.CreateAdminUser(self.token.username)

    self.Open("/")

    self.Click("css=a[grrtarget=globalFlows]")
    self.Click("css=#_Reporting")

    self.assertEqual("RunReport", self.GetText("link=RunReport"))
    self.Click("link=RunReport")
    self.WaitUntil(self.IsTextPresent, "Report name")

  def testDoesNotShowGenerateArchiveButtonForNonExportableRDFValues(self):
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          "FlowWithOneNetworkConnectionResult",
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('FlowWithOneNetworkConnectionResult')")
    self.Click("link=Results")

    self.WaitUntil(self.IsTextPresent, "42")
    self.WaitUntilNot(self.IsTextPresent,
                      "Files referenced in this collection can be downloaded")

  def testDoesNotShowGenerateArchiveButtonWhenResultsCollectionIsEmpty(self):
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          RecursiveTestFlow.__name__,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('RecursiveTestFlow')")
    self.Click("link=Results")

    self.WaitUntil(self.IsTextPresent, "Value")
    self.WaitUntilNot(self.IsTextPresent,
                      "Files referenced in this collection can be downloaded")

  def testShowsGenerateArchiveButtonForGetFileFlow(self):
    pathspec = rdf_paths.PathSpec(
        path=os.path.join(self.base_path, "test.plist"),
        pathtype=rdf_paths.PathSpec.PathType.OS)
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          flows_transfer.GetFile.__name__,
          self.action_mock,
          client_id=self.client_id,
          pathspec=pathspec,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('GetFile')")
    self.Click("link=Results")

    self.WaitUntil(self.IsTextPresent,
                   "Files referenced in this collection can be downloaded")

  def testGenerateArchiveButtonGetsDisabledAfterClick(self):
    pathspec = rdf_paths.PathSpec(
        path=os.path.join(self.base_path, "test.plist"),
        pathtype=rdf_paths.PathSpec.PathType.OS)
    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          flows_transfer.GetFile.__name__,
          self.action_mock,
          client_id=self.client_id,
          pathspec=pathspec,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('GetFile')")
    self.Click("link=Results")
    self.Click("css=button.DownloadButton")

    self.WaitUntil(self.IsElementPresent, "css=button.DownloadButton[disabled]")
    self.WaitUntil(self.IsTextPresent, "Generation has started")

  def testShowsNotificationWhenArchiveGenerationIsDone(self):
    pathspec = rdf_paths.PathSpec(
        path=os.path.join(self.base_path, "test.plist"),
        pathtype=rdf_paths.PathSpec.PathType.OS)
    flow_urn = flow.GRRFlow.StartFlow(
        flow_name=flows_transfer.GetFile.__name__,
        client_id=self.client_id,
        pathspec=pathspec,
        token=self.token)

    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          flow_urn,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#c=C.0000000000000001")

    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('GetFile')")
    self.Click("link=Results")
    self.Click("css=button.DownloadButton")
    self.WaitUntil(self.IsTextPresent, "Generation has started")
    self.WaitUntil(self.IsUserNotificationPresent,
                   "Downloaded archive of flow %s" % flow_urn.Basename())

  def testShowsErrorMessageIfArchiveStreamingFailsBeforeFirstChunkIsSent(self):
    pathspec = rdf_paths.PathSpec(
        path=os.path.join(self.base_path, "test.plist"),
        pathtype=rdf_paths.PathSpec.PathType.OS)
    flow_urn = flow.GRRFlow.StartFlow(
        flow_name=flows_transfer.GetFile.__name__,
        client_id=self.client_id,
        pathspec=pathspec,
        token=self.token)

    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          flow_urn,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    def RaisingStub(*unused_args, **unused_kwargs):
      raise RuntimeError("something went wrong")

    with utils.Stubber(api_call_handler_utils.CollectionArchiveGenerator,
                       "Generate", RaisingStub):
      self.Open("/#c=C.0000000000000001")

      self.Click("css=a[grrtarget='client.flows']")
      self.Click("css=td:contains('GetFile')")
      self.Click("link=Results")
      self.Click("css=button.DownloadButton")
      self.WaitUntil(self.IsTextPresent,
                     "Can't generate archive: Unknown error")
      self.WaitUntil(self.IsUserNotificationPresent,
                     "Archive generation failed for flow %s" %
                     flow_urn.Basename())

  def testShowsNotificationIfArchiveStreamingFailsInProgress(self):
    pathspec = rdf_paths.PathSpec(
        path=os.path.join(self.base_path, "test.plist"),
        pathtype=rdf_paths.PathSpec.PathType.OS)
    flow_urn = flow.GRRFlow.StartFlow(
        flow_name=flows_transfer.GetFile.__name__,
        client_id=self.client_id,
        pathspec=pathspec,
        token=self.token)

    with self.ACLChecksDisabled():
      for _ in test_lib.TestFlowHelper(
          flow_urn,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    def RaisingStub(*unused_args, **unused_kwargs):
      yield "foo"
      yield "bar"
      raise RuntimeError("something went wrong")

    with utils.Stubber(api_call_handler_utils.CollectionArchiveGenerator,
                       "Generate", RaisingStub):
      self.Open("/#c=C.0000000000000001")

      self.Click("css=a[grrtarget='client.flows']")
      self.Click("css=td:contains('GetFile')")
      self.Click("link=Results")
      self.Click("css=button.DownloadButton")

      self.WaitUntil(self.IsUserNotificationPresent,
                     "Archive generation failed for flow %s" %
                     flow_urn.Basename())
      # There will be no failure message, as we can't get a status from an
      # iframe that triggers the download.
      self.WaitUntilNot(self.IsTextPresent,
                        "Can't generate archive: Unknown error")

  def testCreateHuntFromFlow(self):
    email_descriptor = output_plugin.OutputPluginDescriptor(
        plugin_name=email_plugin.EmailOutputPlugin.__name__,
        plugin_args=email_plugin.EmailOutputPluginArgs(
            email_address="test@localhost", emails_limit=42))

    args = flows_processes.ListProcessesArgs(
        filename_regex="test[a-z]*", fetch_binaries=True)

    flow.GRRFlow.StartFlow(
        flow_name=flows_processes.ListProcesses.__name__,
        args=args,
        client_id=self.client_id,
        output_plugins=[email_descriptor],
        token=self.token)

    # Navigate to client and select newly created flow.
    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")
    self.Click("css=td:contains('ListProcesses')")

    # Open wizard and check if flow arguments are copied.
    self.Click("css=button[name=create_hunt]")

    self.WaitUntilEqual("test[a-z]*", self.GetValue,
                        "css=label:contains('Filename Regex') ~ * input")

    self.WaitUntil(self.IsChecked, "css=label:contains('Fetch Binaries') "
                   "~ * input[type=checkbox]")

    # Go to next page and check that we did not copy the output plugins.
    self.Click("css=button:contains('Next')")

    self.WaitUntilNot(self.IsElementPresent,
                      "css=grr-output-plugin-descriptor-form")

    # Nothing else to check, so finish the hunt.
    self.Click("css=button:contains('Next')")
    self.Click("css=button:contains('Next')")
    self.Click("css=button:contains('Create Hunt')")
    self.Click("css=button:contains('Done')")

    # Check that we get redirected to ManageHunts.
    self.WaitUntilEqual(1, self.GetCssCount,
                        "css=grr-hunts-list table tbody tr")
    self.WaitUntilEqual(1, self.GetCssCount,
                        "css=grr-hunts-list table tbody tr.row-selected")
    self.WaitUntil(self.IsTextPresent, "GenericHunt")
    self.WaitUntil(self.IsTextPresent, "ListProcesses")

  def testCheckCreateHuntButtonIsOnlyEnabledWithFlowSelection(self):
    flow.GRRFlow.StartFlow(
        client_id=self.client_id,
        flow_name=RecursiveTestFlow.__name__,
        token=self.token)

    # Open client and find the flow.
    self.Open("/#c=C.0000000000000001")
    self.Click("css=a[grrtarget='client.flows']")

    # No flow selected, so the button should be disabled.
    self.WaitUntil(self.IsElementPresent,
                   "css=button[name=create_hunt][disabled]")

    # And enabled after selecting the test flow.
    self.Click("css=td:contains('RecursiveTestFlow')")
    self.WaitUntil(self.IsElementPresent,
                   "css=button[name=create_hunt]:not([disabled])")

  def testDoesNotShowDownloadAsPanelIfCollectionIsEmpty(self):
    with self.ACLChecksDisabled():
      flow_urn = flow.GRRFlow.StartFlow(
          flow_name=RecursiveTestFlow.__name__,
          client_id=self.client_id,
          token=self.token)
      for _ in test_lib.TestFlowHelper(
          flow_urn,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#/clients/C.0000000000000001/flows/%s" % flow_urn.Basename())
    self.Click("link=Results")

    self.WaitUntil(self.IsTextPresent, "Value")
    self.WaitUntilNot(self.IsElementPresent, "grr-download-collection-as")

  @mock.patch.object(api_call_router_with_approval_checks.
                     ApiCallRouterWithApprovalChecksWithRobotAccess,
                     "GetExportedFlowResults")
  def testClickingOnDownloadAsCsvZipStartsDownload(self, mock_method):
    pathspec = rdf_paths.PathSpec(
        path=os.path.join(self.base_path, "test.plist"),
        pathtype=rdf_paths.PathSpec.PathType.OS)
    with self.ACLChecksDisabled():
      flow_urn = flow.GRRFlow.StartFlow(
          flow_name=flows_transfer.GetFile.__name__,
          client_id=self.client_id,
          pathspec=pathspec,
          token=self.token)
      for _ in test_lib.TestFlowHelper(
          flow_urn,
          self.action_mock,
          client_id=self.client_id,
          token=self.token):
        pass

    self.Open("/#/clients/C.0000000000000001/flows/%s" % flow_urn.Basename())
    self.Click("link=Results")

    self.Click("css=grr-download-collection-as button[name='csv-zip']")

    def MockMethodIsCalled():
      try:
        mock_method.assert_called_once_with(
            api_flow.ApiGetExportedFlowResultsArgs(
                client_id=self.client_id.Basename(),
                flow_id=flow_urn.Basename(),
                plugin_name="csv-zip"),
            token=mock.ANY)

        return True
      except AssertionError:
        return False

    self.WaitUntil(MockMethodIsCalled)


def main(argv):
  # Run the full test suite
  runtests_test.SeleniumTestProgram(argv=argv)


if __name__ == "__main__":
  flags.StartMain(main)
