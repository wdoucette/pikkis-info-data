import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from emulator.lua_tables import Parser
from emulator_update import selected,create_manifest,verify_snapshot,publish
from emulator.catalogue import build

class LiteralTests(unittest.TestCase):
    def test_nested_quotes_and_types(self):
        self.assertEqual(Parser(r'''{[10001]={name="Nikki's \\"hair\\"",n=4,ok=true},[2]=false}'''.replace('\\\\','\\')).val(),
                         {10001:{'name':'Nikki\'s "hair"','n':4,'ok':True},2:False})
    def test_code_rejected(self):
        with self.assertRaises(ValueError):Parser('{x=os.execute("bad")}').val()
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):Parser('{[1]=2,[1]=3}').val()
    def test_archive_allowlist(self):
        for name in ('../clothes_data.lua','clothes_data/../../usrdat/x','usrdat/clothes_date','clothes_data/not_data.lua'):
            self.assertFalse(selected(name),name)
        self.assertTrue(selected('clothes_data/clothes_data_100.lua'))
    def test_publish_requires_review(self):
        with self.assertRaises(ValueError):publish(Path('does-not-exist'),False)
    def test_stable_legacy_ids(self):
        root=Path(__file__).resolve().parents[1]
        registry=json.loads((root/'data/game-id-map.json').read_text('utf-8'))
        ids=registry['game_to_site'];self.assertEqual(ids['10001'],-1)
        self.assertEqual(len(ids.values()),len(set(ids.values())))
        baseline=json.loads((root/'data/pikkis-info.min.json').read_text('utf-8'))
        self.assertFalse({r['id'] for r in baseline['items']}-set(ids.values()))

class CatalogueTests(unittest.TestCase):
    def fixture(self):
        clothes={g:dict(name=str(g),depth_type=1,no_display=int(g==10003),rare=2,desc='',cvt_base=0) for g in (10001,10002,10003)}
        tables={'gdClothesData_100':clothes,'gdClothesDataKey':dict.fromkeys(clothes,1),
            'gdClothesGenreData':{1:{1:10001,2:10002,3:10003}},'gdClothesResolveData':{10001:{'resolve':0},10002:{'resolve':1}},
            'gdClothesMergeData':{10002:{'clothes':{1:{'cloth':10001,'num':3}}},10003:{'clothes':{1:{'cloth':10002,'num':4}}}},
            'gdClothesEvolutionData':{},'gdClothesConvertData':{}}
        return tables,{'game_to_site':{'10001':-1},'next_id':1},{'items':[{'id':-1}]}
    def test_hidden_targets_and_stable_ids(self):
        t,m,b=self.fixture();out,report=build(t,m,b,minimum_items=2)
        self.assertEqual(len(out['items']),2)
        a,c=out['items'];self.assertEqual((a['id'],a['rd'],a['rr']),(-1,3,4))
        self.assertTrue(a['cannot_decompose']);self.assertEqual(c['fut'],1)
        self.assertEqual(c['cf'],[{'id':-1,'q':3}]);self.assertEqual(c['rd'],0)
    def test_unavailable_recipe_not_counted(self):
        t,m,b=self.fixture();t['gdClothesMergeData'][10002]['no_display']=1
        out,_=build(t,m,b,minimum_items=2)
        self.assertEqual(out['items'][0]['rd'],0);self.assertEqual(out['items'][0]['fut'],1)
    def test_unknown_dependency_blocks(self):
        t,m,b=self.fixture();t['gdClothesMergeData'][10002]['clothes'][1]['cloth']=99999
        with self.assertRaises(ValueError):build(t,m,b,minimum_items=2)
    def test_disappearing_published_item_blocks(self):
        t,m,b=self.fixture();b['items'].append({'id':123})
        with self.assertRaises(ValueError):build(t,m,b,minimum_items=2)

if __name__=='__main__':unittest.main()
