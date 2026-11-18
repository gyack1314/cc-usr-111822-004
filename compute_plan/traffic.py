from .evidence import at
def requests(db, known_at):
    rows=[]
    for item in at(db,'load',known_at):
        p=item['payload']
        if p['requests']<0:raise ValueError('请求数不得为负')
        rows.append({'window':p['window'],'region':p['region'],'class':p['class'],'requests':p['requests'],'source':item['digest']})
    return rows
