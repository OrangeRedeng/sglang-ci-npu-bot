"""Keep evidence requests valid JSON under the model's context budget."""
import copy
import json


def bounded_json(value, limit):
    data=copy.deepcopy(value)
    def encode():return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    text=encode()
    if len(text)<=limit:return text
    data['evidence_capped']=True
    while len(text)>limit:
        lists=[];strings=[]
        def visit(obj):
            if isinstance(obj,dict):
                for k,v in obj.items():
                    if isinstance(v,str) and len(v)>256:strings.append((len(v),obj,k))
                    else:visit(v)
            elif isinstance(obj,list):
                if len(obj)>1:lists.append((len(json.dumps(obj,default=str)),obj))
                for v in obj:visit(v)
        visit(data)
        if lists:
            target=max(lists,key=lambda x:x[0])[1]
            del target[max(1,len(target)//2):]
        elif strings:
            _,parent,key=max(strings,key=lambda x:x[0]);parent[key]=parent[key][:max(256,len(parent[key])//2)]+' [excerpt capped]'
        else:raise ValueError('Required evidence exceeds context budget')
        text=encode()
    return text
