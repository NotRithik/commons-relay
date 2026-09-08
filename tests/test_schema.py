import unittest
from commons_relay.schema import check_schema,validate
from commons_relay.skills import Skill,Registry,Quote,default_registry
from commons_relay.codec import Rejected

class SchemaTests(unittest.TestCase):
    def test_every_default_skill_descriptor_supported(self):
        for skill in default_registry().describe():check_schema(skill['input_schema'])
    def test_extra_keywords_not_silently_ignored(self):
        with self.assertRaises(Rejected):check_schema({'type':'string','mysteriousRule':1})
    def test_unsupported_types_rejected(self):
        with self.assertRaises(Rejected):check_schema({'type':'number'})
    def test_boolean_not_integer(self):
        with self.assertRaises(Rejected):validate(True,{'type':'integer'})
    def test_string_size_enforced(self):
        with self.assertRaises(Rejected):validate('too long',{'type':'string','maxLength':3})
    def test_decimal_money_schema(self):
        schema=default_registry().get('wallet.send').input_schema
        for amount in [1,True,'1.0','01','-1',' 2']:
            with self.subTest(amount=amount):
                with self.assertRaises(Rejected):validate({'recipient':'bob','amount':amount},schema)
    def test_extra_tool_argument_rejected(self):
        with self.assertRaises(Rejected):default_registry().get('storage.upload').validate({'path':'file','label':'x','shell':'bad'})
    def test_missing_tool_argument_rejected(self):
        with self.assertRaises(Rejected):default_registry().get('storage.upload').validate({'path':'file'})
    def test_zero_cost_skills_still_type_checked(self):
        with self.assertRaises(Rejected):default_registry().get('storage.upload').validate({'path':True,'label':'x'})
    def test_array_types_and_unique(self):
        skill=default_registry().get('messaging.create_group')
        with self.assertRaises(Rejected):skill.validate({'members':['same','same']})
        with self.assertRaises(Rejected):skill.validate({'members':[]})
        with self.assertRaises(Rejected):skill.validate({'members':'wrong'})
    def test_integer_cursor_bounds(self):
        schema=default_registry().get('agent.subscribe').input_schema
        with self.assertRaises(Rejected):validate({'agent_address':'a','task_id':'t','cursor':-1},schema)
    def test_new_custom_skill_schema_registered_without_engine_changes(self):
        custom=Skill('observatory.temperature','Custom fixture',('station',),lambda _:Quote('LEZ-testnet',0),False,{'type':'object','properties':{'station':{'type':'string','enum':['north','south']}},'required':['station'],'additionalProperties':False})
        registry=Registry([custom]);self.assertEqual(registry.get(custom.name).validate({'station':'north'}).maximum,0)
        with self.assertRaises(Rejected):registry.get(custom.name).validate({'station':'arbitrary'})
    def test_custom_schema_must_be_valid_before_registration(self):
        custom=Skill('test.custom','bad',('x',),lambda _:Quote('LEZ-testnet',0),False,{'type':'object','required':['missing']})
        with self.assertRaises(Rejected):Registry([custom])
    def test_malformed_regex_rejected_on_registration(self):
        with self.assertRaises(Rejected):check_schema({'type':'string','pattern':'['})
    def test_nested_objects_validated(self):
        schema={'type':'object','properties':{'x':{'type':'array','items':{'type':'boolean'}}},'required':['x'],'additionalProperties':False}
        check_schema(schema);validate({'x':[True,False]},schema)
        with self.assertRaises(Rejected):validate({'x':[1]},schema)
if __name__=='__main__':unittest.main()
