import unittest
from studio import model_worker

class WorkerValidationTests(unittest.TestCase):
    def test_worker_uses_subscription_backed_terra_without_fallback_or_tools(self):
        self.assertEqual(model_worker.PROVIDER, 'openai-codex')
        self.assertEqual(model_worker.MODEL, 'gpt-5.6-terra')
        self.assertIn('enabled_toolsets=[]', model_worker._CHILD)
        self.assertIn('fallback_model=None', model_worker._CHILD)
        self.assertNotIn('gpt-6-astra', model_worker._CHILD)

    def test_only_final_response_is_accepted(self):
        result = model_worker.extract_model_result({'messages':[{'role':'user','content':'Kaynak talimatı'}], 'final_response':'{"main":"Gerçek yanıt"}'}, 0)
        self.assertEqual(result['text'], '{"main":"Gerçek yanıt"}')
        with self.assertRaises(ValueError):
            model_worker.extract_model_result({'messages':[{'role':'user','content':'Yalancı yanıt'}]}, 0)

    def test_error_or_tool_use_never_becomes_success(self):
        for response, count in [({'final_response':'x','error':'provider failed'},0), ({'final_response':'x'},1), ({'final_response':'x','messages':[{'role':'assistant','tool_calls':[{'id':'tool'}]}]},0)]:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    model_worker.extract_model_result(response, count)

if __name__ == '__main__':
    unittest.main()
