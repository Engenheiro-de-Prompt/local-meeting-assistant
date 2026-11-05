"""Deferred UI recovery against synthetic API/DOM, without running inference."""
import pathlib
import tempfile
import unittest

import test_resource_safety_ui as resource_ui
from test_processing import make_manager


class DeferredRecoveryUiTests(unittest.TestCase):
    def run_js(self, code):
        harness = resource_ui.ResourceSafetyUiTests()
        harness.setUp()
        harness.run_js(code)

    def test_safe_explicit_resume_reads_back_and_never_autoresumes(self):
        self.run_js('''
const s={state:'processing',session_id:'synthetic',pending_final_processing:true,processing_worker_alive:false,resource_guard:{level:0,recording_only:false,transcription_allowed:true,capture_alive:false}};
latestStatus=s;renderResourceGuard(s);assert.equal(posts.length,0);assert.equal(nodes.resumeProcessing.disabled,false);
await resumeProcessing();assert.equal(posts[0].path,'/api/resume');assert.equal(posts[0].body.session_id,'synthetic');assert.equal(posts[1].path,'/api/status');
''')

    def test_pause_clear_is_separate_and_does_not_resume(self):
        self.run_js('''
latestStatus={state:'processing',session_id:'synthetic',pending_final_processing:true,processing_worker_alive:false,resource_guard:{recording_only:true,capture_alive:false,transcription_allowed:false}};
renderResourceGuard(latestStatus);assert.equal(nodes.clearRecordingOnly.disabled,false);assert.equal(nodes.resumeProcessing.disabled,true);
await resumeProcessing();assert.equal(posts.length,0);await clearRecordingOnly();assert.equal(posts.length,2);assert.equal(posts[0].path,'/api/resource-guard');assert.equal(posts[0].body.recording_only,false);assert.equal(posts[1].path,'/api/status');
''')

    def test_recovery_fails_closed_for_unknown_stale_pressure_or_live_workers(self):
        self.run_js('''
const base={state:'processing',session_id:'synthetic',pending_final_processing:true,processing_worker_alive:false,resource_guard:{recording_only:false,transcription_allowed:true,capture_alive:false}};
for(const patch of [{resource_guard:undefined},{resource_guard:{}},{processing_worker_alive:true},{processing_worker_alive:undefined},{pending_final_processing:false},{resource_guard:{...base.resource_guard,transcription_allowed:false}},{resource_guard:{...base.resource_guard,telemetry_stale:true}},{resource_guard:{...base.resource_guard,capture_alive:true}},{resource_guard:{...base.resource_guard,recording_only:true}}]){latestStatus={...base,...patch};renderResourceGuard(latestStatus);assert.equal(nodes.resumeProcessing.disabled,true);await resumeProcessing();assert.equal(posts.length,0)}
''')

    def test_resume_duplicate_click_and_server_rejection_preserve_recovery(self):
        self.run_js('''
latestStatus={state:'processing',session_id:'synthetic',pending_final_processing:true,processing_worker_alive:false,resource_guard:{recording_only:false,transcription_allowed:true,capture_alive:false}};
const stopped=latestStatus;let release,count=0;api=async(path)=>{if(path==='/api/resume'){count++;await new Promise(r=>release=r);throw Error('synthetic pressure')}return stopped};
const pending=resumeProcessing();assert.equal(nodes.resumeProcessing.disabled,true);await resumeProcessing();assert.equal(count,1);release();await pending;assert.match(nodes.resourceGuardError.textContent,/synthetic pressure/);assert.equal(latestStatus.pending_final_processing,true);assert.equal(nodes.resumeProcessing.disabled,false);
''')

    def test_status_exposes_pending_and_worker_liveness(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'synthetic')
            assert manager.session is not None
            manager.session.update(state='processing', resource_deferred=True)
            status = manager.status()
            self.assertTrue(status['resource_deferred'])
            self.assertTrue(status['pending_final_processing'])
            self.assertFalse(status['processing_worker_alive'])
            manager.session.update(state='completed', resource_deferred=False)
            self.assertFalse(manager.status()['pending_final_processing'])


if __name__ == '__main__':
    unittest.main()
