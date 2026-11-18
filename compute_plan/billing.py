from .evidence import at
from .capacity import utilization
def month(db, known_at):
    prices={(r['payload']['region'],r['payload']['month']):r for r in at(db,'price',known_at)};out=[]
    for row in utilization(db,known_at):
        price=prices.get((row['region'],row['window'][:7]))
        if not price or 'capacity' not in row:out.append({'window':row['window'],'status':'账单待核'});continue
        out.append({'window':row['window'],'estimated_energy_cost':row['capacity']*price['payload']['energy_per_unit']*price['payload']['tariff'],'sources':[row['source'],row['pool_source'],price['digest']]})
    return out
