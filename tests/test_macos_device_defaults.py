import json
import pathlib
import subprocess
import unittest

class MacDeviceDefaultsTests(unittest.TestCase):
    def load_devices(self, monitors, prefs=None):
        page = (pathlib.Path(__file__).resolve().parents[1]/'web/index.html').read_text()
        function = next(line for line in page.splitlines() if line.startswith('async function loadDevices()'))
        devices = {'capture_backend':'avfoundation','microphones':[{'id':'2','desc':'MacBook Pro Microphone'}],'monitors':monitors}
        script = 'const elements={device:{value:"cuda"},mode:{value:"both"},mic:{id:"mic"},pc:{id:"pc"}}; const $=id=>elements[id]; let savedPrefs='+json.dumps(prefs or {})+'; const data='+json.dumps(devices)+'; const api=async()=>data;const selections={};function optionSelect(select,items,selected){selections[select.id]=selected};const persistPrefs=()=>{};'+function+';loadDevices().then(()=>console.log(JSON.stringify({mode:elements.mode.value,selections})))'
        script += ';if(savedPrefs.mode)elements.mode.value=savedPrefs.mode;'
        result = subprocess.run(['node','-e',script],text=True,capture_output=True,timeout=10,check=True)
        return json.loads(result.stdout)

    def test_fresh_mac_with_loopback_defaults_to_mic_plus_system(self):
        result=self.load_devices([{'id':'1','desc':'BlackHole 2ch'}])
        self.assertEqual(result['mode'],'both')

    def test_real_blackhole_is_preferred_over_aggregate_device(self):
        result=self.load_devices([{'id':'0','name':'Reuniões Locais (Som + BlackHole)','desc':'Reuniões Locais (Som + BlackHole)'},{'id':'1','name':'BlackHole 2ch','desc':'BlackHole 2ch'}])
        self.assertEqual(result['selections']['pc'],'1')

    def test_user_explicit_microphone_preference_is_preserved(self):
        page_result=self.load_devices([{'id':'1','desc':'BlackHole 2ch'}],{'captureBackend':'avfoundation','mode':'mic_only'})
        self.assertEqual(page_result['mode'],'mic_only')

    def test_mac_without_loopback_still_defaults_to_microphone(self):
        self.assertEqual(self.load_devices([])['mode'],'mic_only')

if __name__=='__main__':unittest.main()
