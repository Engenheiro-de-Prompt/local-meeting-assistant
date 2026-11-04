import json,pathlib,subprocess,unittest

class ProcessingProgressUiTests(unittest.TestCase):
    def label(self,status):
        page=(pathlib.Path(__file__).resolve().parents[1]/'web/index.html').read_text()
        function=next((line for line in page.splitlines() if line.startswith('function processingProgressLabel(s)')),None)
        self.assertIsNotNone(function,'Dashboard must show transcription backlog instead of appearing stuck')
        assert function is not None
        script=function+';console.log(processingProgressLabel('+json.dumps(status)+'));'
        result=subprocess.run(['node','-e',script],text=True,capture_output=True,timeout=10,check=True)
        return result.stdout.strip()

    def test_backlog_and_independent_analysis_are_visible(self):
        label=self.label({'segments_done':21,'processing_progress':{'next_chunk':21,'captured_chunk_count':164,'open_chunk_count':0,'pending_chunk_count':143,'analysis_busy':True}})
        self.assertIn('21/164',label)
        self.assertIn('143 pendentes',label)
        self.assertIn('análise em segundo plano',label)

if __name__=='__main__':unittest.main()
