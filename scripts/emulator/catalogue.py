"""Map game static tables to the site's stable item IDs and compact schema."""
import collections
from pathlib import Path
from .lua_tables import tables
from scrape_lovenikki import SLOT_NAMES, _times_crafted, compact_bundle, load_and_apply_overrides

def load_tables(decoded):
    result={}
    for path in sorted(Path(decoded).rglob('*.lua')):
        for name,value in tables(path).items():
            if name in result and result[name]!=value:raise ValueError(f'Duplicate table {name}')
            result[name]=value
    return result

def build(t, registry, baseline, overrides=None, minimum_items=30000):
    all_items={}
    for name,value in t.items():
        if name.startswith('gdClothesData_'):all_items.update(value)
    if set(all_items)!=set(t['gdClothesDataKey']):raise ValueError('Clothing shards do not match master index')
    visible={gid:r for gid,r in all_items.items() if r['no_display']==0}
    if len(visible)<minimum_items:raise ValueError('Incomplete catalogue: visible item count is too small')
    ids={int(k):v for k,v in registry['game_to_site'].items()}
    if len(set(ids.values()))!=len(ids):raise ValueError('Duplicate stable IDs')
    if registry['next_id']<=max(ids.values()):raise ValueError('Invalid next_id')
    for gid in sorted(visible):
        if gid not in ids:ids[gid]=registry['next_id'];registry['next_id']+=1
    old_ids={r['id'] for r in baseline['items']}
    if old_ids-set(ids[g] for g in visible):raise ValueError('Previously published items disappeared; manual investigation required')
    registry['game_to_site']={str(k):v for k,v in sorted(ids.items())}
    genres={gid:genre for genre,members in t['gdClothesGenreData'].items() for gid in members.values()}
    rows={}; game_for_site={}
    for gid,r in sorted(visible.items(),key=lambda pair:ids[pair[0]]):
        slot=r['depth_type']-1
        if slot not in SLOT_NAMES:raise ValueError(f'Unknown clothing slot {slot}')
        genre=88 if slot==60 else (gid//10000)%10
        vis=gid-880000 if genre==88 else gid-genre*10000-(gid//100000)*90000
        sid=ids[gid];game_for_site[sid]=gid
        rows[sid]=dict(id=sid,vis=vis,name=r['name'],slot=slot,category=SLOT_NAMES[slot],rarity=r['rare'],desc=r['desc'],
            used_in=[],recipe=[],evolves_from=[],evolves_to=[],customisations=[],future_recipe=False,
            cannot_decompose=t['gdClothesResolveData'].get(gid,{}).get('resolve')!=1)
    future_edges=0
    def connect(src,dst,qty,kind,available=True):
        nonlocal future_edges
        if src not in all_items or dst not in all_items:raise ValueError(f'Unknown recipe/evolution ID {src}->{dst}')
        if not isinstance(qty,int) or qty<=0:raise ValueError('Non-positive quantity')
        if src not in visible:
            if dst in visible and available:raise ValueError(f'Visible product has hidden ingredient {src}->{dst}')
            return
        if dst not in visible or not available:
            rows[ids[src]]['future_recipe']=True;future_edges+=1;return
        a,b=rows[ids[src]],rows[ids[dst]]
        if kind=='recipe':
            b['recipe'].append({'id':a['id'],'num':qty});a['used_in'].append({'id':b['id'],'num':qty})
        else:
            b['evolves_from'].append({'id':a['id'],'num':qty});a['evolves_to'].append({'id':b['id'],'num':qty})
    for gid,recipe in t['gdClothesMergeData'].items():
        for ingredient in recipe.get('clothes',{}).values():
            connect(ingredient['cloth'],gid,ingredient['num'],'recipe',recipe.get('no_display',0)==0)
    for gid,evolution in t['gdClothesEvolutionData'].items():connect(evolution['src'],gid,evolution['num'],'evolution')
    groups=collections.defaultdict(list)
    for gid,r in all_items.items():
        if r.get('cvt_base'):groups[r['cvt_base']].append(gid)
    for group in groups.values():
        for gid in group:
            if gid not in visible:continue
            for dst in group:
                if dst==gid:continue
                if dst not in visible:rows[ids[gid]]['future_recipe']=True;continue
                conversion=t['gdClothesConvertData'].get(dst)
                if conversion is None:raise ValueError(f'Missing recolour target {dst}')
                rows[ids[gid]]['customisations'].append(dict(dyedItem=ids[dst],name=conversion['display_name']))
    # Preserve the existing site's count convention. These counts are not an owned-aware safe-to-decompose verdict.
    times=_times_crafted(rows,False); times_custom=_times_crafted(rows,True)
    for sid,row in rows.items():
        edges={u['id']:u['num'] for u in row['used_in']+row['evolves_to']}
        custom=dict(edges)
        for dye in row['customisations']:custom.setdefault(dye['dyedItem'],1)
        row.update(used_to_craft=[dict(id=k,name=rows[k]['name'],num=v) for k,v in edges.items()],
            required_direct=sum(edges.values()),required_direct_custom=sum(custom.values()),
            required_recursive=1+sum(q*times[k] for k,q in edges.items()),
            required_recursive_custom=1+sum(q*times_custom[k] for k,q in custom.items()))
    if overrides:load_and_apply_overrides(rows,str(overrides),None,delay=0)
    bundle=compact_bundle(rows,{'primary_source':'local-emulator'})
    suit_links=collections.defaultdict(list);suits=[]
    for aid,ach in t.get('gdAchievementDetailData',{}).items():
        members=list(ach.get('clothes',{}).values())
        if ach.get('type')!=1 or not members or ach.get('isDelete') or ach.get('no_display') or any(g not in visible for g in members):continue
        suit=dict(id=aid,name=ach['name'],items=[ids[g] for g in members])
        reward_gids=[g for g,a in t.get('gdSuitAchievementRewardsComplex',{}).items() if a==aid and g in visible]
        if reward_gids:suit['reward_items']=[ids[g] for g in reward_gids]
        variants=[]
        for variant in t.get('gdSuitConvertData',{}).get(aid,{}).values():
            parts=list(variant.get('clothes',{}).values())
            if parts and all(g in visible for g in parts):variants.append(dict(id=variant['id'],items=[ids[g] for g in parts]))
        if variants:suit['variants']=variants
        suits.append(suit)
        for gid in members+reward_gids:suit_links[gid].append(aid)
    bundle['suits']=suits
    for row in bundle['items']:
        gid=game_for_site[row['id']];row['game_id']=gid
        row['description']=visible[gid]['desc']
        if suit_links[gid]:row['suit_ids']=sorted(set(suit_links[gid]))
        recipe=t['gdClothesMergeData'].get(gid)
        if recipe:
            row['recipe_available']=recipe.get('no_display',0)==0
            if not row['recipe_available']:
                row['pending_recipe']=[dict(game_id=v['cloth'],id=ids.get(v['cloth']),q=v['num']) for v in recipe['clothes'].values()]
            else:
                cost=t.get('gdClothesRareData',{}).get(visible[gid]['rare'],{}).get('merge_cost')
                if cost is not None:row['craft_gold']=cost
        for dye in row.get('d',[]):
            target=game_for_site[dye['id']];conversion=t['gdClothesConvertData'][target]
            material=t.get('gdItemData',{}).get(conversion['item'],{})
            dye.update(dye_game_id=conversion['item'],dye_name=material.get('name'),dye_quantity=conversion['num'],material_quantity=conversion['price'])
        shop=t.get('gdClothesShopData',{}).get(gid)
        if shop and not shop.get('no_display'):
            goods=t.get('gdCostToGoods',{}).get(shop['price_type'])
            currency=t.get('gdGoodsTypeProperty',{}).get(goods,{}).get('name')
            row['shop']=dict(price=shop['price'],currency=currency or f"Currency code {shop['price_type']}",
                currency_code=shop['price_type'],unlock_task_id=shop.get('unlock_taskid',0),
                event_goods=bool(shop.get('is_activity_goods')),availability='configured; check current in-game availability')
        # Editorial changes cannot make a game-forbidden item decomposable.
        if t['gdClothesResolveData'].get(row['game_id'],{}).get('resolve')!=1:row['cannot_decompose']=True
    before={r['id']:r for r in baseline['items']}
    changes=collections.Counter()
    for row in bundle['items']:
        old=before.get(row['id'],{})
        for key in ('name','n','cat','r','rd','rdc','rr','rrc','cf','ev','d','fut','cannot_decompose'):
            if old.get(key)!=row.get(key):changes[key]+=1
    report=dict(items=len(rows),hidden_items=len(all_items)-len(visible),new_items=len(set(rows)-old_ids),
        future_dependency_edges=future_edges,recipe_items=sum(bool(r['recipe']) for r in rows.values()),
        cannot_decompose=sum(bool(r.get('cannot_decompose')) for r in bundle['items']),changed_fields=dict(changes),
        suits=len(suits),items_with_suits=sum(bool(r.get('suit_ids')) for r in bundle['items']),
        shop_entries=sum('shop' in r for r in bundle['items']),descriptions=len(rows),
        dye_cost_links=sum(len(r.get('d',[])) for r in bundle['items']),
        unavailable_recipes=sum(r.get('recipe_available') is False for r in bundle['items']))
    return bundle,report
