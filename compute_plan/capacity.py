from .evidence import at
from .traffic import requests
def utilization(db, known_at):
    pools={(r['payload']['region'],r['payload']['window']):r for r in at(db,'pool',known_at)};out=[]
    for row in requests(db,known_at):
        pool=pools.get((row['region'],row['window']))
        if not pool:out.append({**row,'status':'容量待核'});continue
        size=pool['payload']['capacity']
        out.append({**row,'capacity':size,'utilization':row['requests']/size if size else None,'pool_source':pool['digest']})
    return out
