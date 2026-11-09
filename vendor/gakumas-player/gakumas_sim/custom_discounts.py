"""Bind already-issued item discounts into observed ordinary-shop rate lists."""
from .core import Unsupported
from .growth_scaling import checked

FIELD='custom_shop_discounts'
FIELDS={'item_id','number','activation','source_id','resource','rate'}


def exact(value,fields,label):
    if not isinstance(value,dict) or set(value)!=fields:raise Unsupported(label+' requires exact fields')


def validate_sources(state,sources):
    entries=state.get(FIELD,[])
    if not isinstance(entries,list):raise Unsupported('Custom shop discounts require an ordered list')
    seen=set()
    for entry in entries:
        exact(entry,FIELDS,'Issued custom discount')
        for k in ('item_id','source_id','resource'):
            if not isinstance(entry[k],str):raise Unsupported('Custom discount identities must be strings')
        checked(entry['number'],'Discount item number');checked(entry['activation'],'Discount activation')
        checked(entry['rate'],'Discount permil',1000)
        key=(entry['number'],entry['item_id']);pair=sources.get(key)
        identity=key+(entry['activation'],entry['source_id'])
        if identity in seen or pair is None or not 1<=entry['activation']<=pair[0]['trigger_count']:
            raise Unsupported('Custom discount source or activation mismatch')
        seen.add(identity)
        if not any(e.get('kind')=='shop_discount' and e['source_id']==entry['source_id']
                   and e['resource']==entry['resource'] and e['rate']==entry['rate'] for e in pair[1]['effects']):
            raise Unsupported('Custom discount differs from source effect')


def validate_bindings(snapshot,state):
    discounts=state.get(FIELD,[])
    if not discounts and 'custom_discount_bindings' not in snapshot:return
    bindings=snapshot.get('custom_discount_bindings')
    exact(bindings,{str(p['positionNumber']) for p in snapshot['products']},'Custom discount product bindings')
    for product in snapshot['products']:
        rows=bindings[str(product['positionNumber'])]
        if not isinstance(rows,list):raise Unsupported('Custom discount bindings require lists')
        expected=[i for i,d in enumerate(discounts) if d['resource']==product['resourceType']]
        seen=set();positions=[]
        for row in rows:
            exact(row,{'discount_index','effect_index'},'Current custom discount binding')
            di=checked(row['discount_index'],'Custom discount index');ei=checked(row['effect_index'],'Effect discount index')
            if di not in expected or di in seen or ei>=len(product['effectResourceDiscountPermils']):
                raise Unsupported('Custom discount binding is absent, duplicated or outside current resource')
            if product['effectResourceDiscountPermils'][ei]!=discounts[di]['rate']:
                raise Unsupported('Observed discount rate differs from issued source')
            seen.add(di);positions.append(ei)
        if [r['discount_index'] for r in rows]!=expected or len(set(positions))!=len(positions) or positions!=sorted(positions):
            raise Unsupported('Known custom discounts are missing, duplicated or out of order')
