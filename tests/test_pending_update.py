import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from check_pending_update import classify,rc4

class PendingTests(unittest.TestCase):
    def response(self,patches):return {'ret':0,'data':{'chn':{'id':12,'v_notice':1},'vlist':patches}}
    def test_current(self):self.assertEqual(classify(self.response([]),'11.4.1')['status'],'current')
    def test_pending(self):
        result=classify(self.response([{'version':'11.4.1'},{'version':'11.10.0'}]),'11.3.0')
        self.assertTrue(result['pending_update']);self.assertEqual(result['target_resource_version'],'11.10.0')
    def test_missing_list_is_not_current(self):
        r=self.response([]);del r['data']['vlist']
        with self.assertRaises(ValueError):classify(r,'11.4.1')
    def test_error_is_not_current(self):
        with self.assertRaises(ValueError):classify({'ret':1},'11.4.1')
    def test_suppressed_channel_is_not_current(self):
        r=self.response([]);r['data']['chn']['v_notice']=0
        with self.assertRaises(ValueError):classify(r,'11.4.1')
    def test_old_patch_is_rejected(self):
        with self.assertRaises(ValueError):classify(self.response([{'version':'11.3.0'}]),'11.4.1')
    def test_cipher_known_vector(self):self.assertEqual(rc4(b'Plaintext',b'Key').hex(),'bbf316e8d940af0ad3')

if __name__=='__main__':unittest.main()
