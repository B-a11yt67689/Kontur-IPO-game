import sqlite3
from uuid import uuid4
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

DB = 'ipo.sqlite3'
ROLES = {'cfo', 'cto', 'people', 'cmo'}
STEPS = ['development', 'hiring', 'resources', 'product_experiments', 'marketing', 'clients', 'expenses', 'pnl', 'valuation']

def conn():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

@asynccontextmanager
async def lifespan(app):
    with conn() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS games(id TEXT PRIMARY KEY, title TEXT, host_token TEXT, act INT DEFAULT 0, step INT DEFAULT 0, started INT DEFAULT 0);
        CREATE TABLE IF NOT EXISTS teams(id TEXT PRIMARY KEY, game_id TEXT, name TEXT, cash INT DEFAULT 675, points INT DEFAULT 130, clients INT DEFAULT 60);
        CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY, team_id TEXT, name TEXT, role TEXT, token TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, team_id TEXT, act INT, step INT, domain TEXT, author_id TEXT, content TEXT);
        CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, proposal_id TEXT UNIQUE, status TEXT DEFAULT 'approved');
        ''')
    yield

app = FastAPI(title='Выход на IPO — MVP', lifespan=lifespan)

class Name(BaseModel):
    name: str
class Member(BaseModel):
    name: str
    role: str
class Proposal(BaseModel):
    domain: str
    content: str
class Metrics(BaseModel):
    cash: int | None = None
    points: int | None = None
    clients: int | None = None

def actor(authorization):
    token = (authorization or '').removeprefix('Bearer ').strip()
    with conn() as db:
        g = db.execute('SELECT * FROM games WHERE host_token=?', (token,)).fetchone()
        if g:
            return {'kind': 'host', 'game_id': g['id']}
        m = db.execute('SELECT m.*,t.game_id FROM members m JOIN teams t ON m.team_id=t.id WHERE token=?', (token,)).fetchone()
        if m:
            return dict(m) | {'kind': 'member'}
    raise HTTPException(401, 'Invalid bearer token')

def host(authorization, game_id):
    a = actor(authorization)
    if a['kind'] != 'host' or a['game_id'] != game_id:
        raise HTTPException(403, 'Host only')
    return a

def game(db, game_id):
    g = db.execute('SELECT * FROM games WHERE id=?', (game_id,)).fetchone()
    if not g:
        raise HTTPException(404, 'Game not found')
    return g

@app.post('/games')
def create_game(data: Name):
    gid, token = str(uuid4()), str(uuid4())
    with conn() as db:
        db.execute('INSERT INTO games(id,title,host_token) VALUES(?,?,?)', (gid,data.name,token))
    return {'game_id': gid, 'host_token': token}

@app.post('/games/{game_id}/teams')
def add_team(game_id: str, data: Name, authorization: str | None = Header(None)):
    host(authorization,game_id)
    tid = str(uuid4())
    with conn() as db:
        db.execute('INSERT INTO teams(id,game_id,name) VALUES(?,?,?)',(tid,game_id,data.name))
    return {'team_id':tid}

@app.post('/teams/{team_id}/members')
def add_member(team_id: str, data: Member, authorization: str | None = Header(None)):
    if data.role not in ROLES:
        raise HTTPException(422,'role: cfo, cto, people, cmo')
    with conn() as db:
        t = db.execute('SELECT * FROM teams WHERE id=?',(team_id,)).fetchone()
        if not t:
            raise HTTPException(404,'Team not found')
        host(authorization,t['game_id'])
        if db.execute('SELECT 1 FROM members WHERE team_id=? AND role=?',(team_id,data.role)).fetchone():
            raise HTTPException(409,'Role already taken')
        mid,token = str(uuid4()),str(uuid4())
        db.execute('INSERT INTO members VALUES(?,?,?,?,?)',(mid,team_id,data.name,data.role,token))
    return {'member_id':mid,'member_token':token,'role':data.role}

@app.post('/games/{game_id}/start')
def start(game_id: str, authorization: str | None = Header(None)):
    host(authorization,game_id)
    with conn() as db:
        game(db,game_id)
        db.execute('UPDATE games SET started=1 WHERE id=?',(game_id,))
    return {'started':True}

@app.get('/games/{game_id}')
def dashboard(game_id: str, authorization: str | None = Header(None)):
    a = actor(authorization)
    if a['game_id'] != game_id:
        raise HTTPException(403,'Wrong game')
    with conn() as db:
        g = dict(game(db,game_id))
        g.pop('host_token')
        g['step_name'] = STEPS[g['step']]
        query = 'SELECT * FROM teams WHERE game_id=?'
        args = [game_id]
        if a['kind']=='member':
            query += ' AND id=?'
            args.append(a['team_id'])
        g['teams'] = [dict(t) for t in db.execute(query,args)]
        for t in g['teams']:
            t['members'] = [dict(m) for m in db.execute('SELECT id,name,role FROM members WHERE team_id=?',(t['id'],))]
        return g

@app.post('/games/{game_id}/proposals')
def propose(game_id: str, data: Proposal, authorization: str | None = Header(None)):
    a = actor(authorization)
    if a['kind']!='member' or a['game_id']!=game_id:
        raise HTTPException(403,'Members only')
    if data.domain not in ROLES or not data.content.strip():
        raise HTTPException(422,'Invalid domain or empty content')
    with conn() as db:
        g = game(db,game_id)
        if not g['started']:
            raise HTTPException(409,'Game not started')
        pid = str(uuid4())
        db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?)',(pid,a['team_id'],g['act'],g['step'],data.domain,a['id'],data.content))
    return {'proposal_id':pid,'status':'proposed'}

@app.get('/games/{game_id}/proposals')
def proposals(game_id: str, authorization: str | None = Header(None)):
    a = actor(authorization)
    if a['game_id']!=game_id:
        raise HTTPException(403,'Wrong game')
    with conn() as db:
        g = game(db,game_id)
        sql = 'SELECT p.*,d.id AS decision_id,d.status FROM proposals p JOIN teams t ON t.id=p.team_id LEFT JOIN decisions d ON d.proposal_id=p.id WHERE t.game_id=? AND p.act=? AND p.step=?'
        args = [game_id,g['act'],g['step']]
        if a['kind']=='member':
            sql += ' AND p.team_id=?'
            args.append(a['team_id'])
        return [dict(r) for r in db.execute(sql,args)]

@app.post('/proposals/{proposal_id}/approve')
def approve(proposal_id: str, authorization: str | None = Header(None)):
    a = actor(authorization)
    if a['kind']!='member':
        raise HTTPException(403,'Members only')
    with conn() as db:
        p = db.execute('SELECT * FROM proposals WHERE id=?',(proposal_id,)).fetchone()
        if not p:
            raise HTTPException(404,'Proposal not found')
        if p['team_id']!=a['team_id'] or p['domain']!=a['role']:
            raise HTTPException(403,'Only responsible role can approve')
        g = game(db,a['game_id'])
        if (p['act'],p['step']) != (g['act'],g['step']):
            raise HTTPException(409,'Old proposal')
        existing = db.execute("SELECT 1 FROM decisions d JOIN proposals p ON p.id=d.proposal_id WHERE p.team_id=? AND p.act=? AND p.step=? AND p.domain=?",(p['team_id'],p['act'],p['step'],p['domain'])).fetchone()
        if existing:
            raise HTTPException(409,'Domain already approved')
        did = str(uuid4())
        db.execute('INSERT INTO decisions(id,proposal_id) VALUES(?,?)',(did,proposal_id))
    return {'decision_id':did,'status':'approved'}

@app.post('/decisions/{decision_id}/confirm')
def confirm(decision_id: str, authorization: str | None = Header(None)):
    with conn() as db:
        r = db.execute('SELECT d.*,t.game_id,p.act,p.step FROM decisions d JOIN proposals p ON p.id=d.proposal_id JOIN teams t ON t.id=p.team_id WHERE d.id=?',(decision_id,)).fetchone()
        if not r:
            raise HTTPException(404,'Decision not found')
        host(authorization,r['game_id'])
        g = game(db,r['game_id'])
        if (r['act'],r['step'])!=(g['act'],g['step']) or r['status']!='approved':
            raise HTTPException(409,'Decision cannot be confirmed')
        db.execute("UPDATE decisions SET status='confirmed' WHERE id=?",(decision_id,))
    return {'status':'confirmed'}

@app.patch('/teams/{team_id}/metrics')
def metrics(team_id: str, data: Metrics, authorization: str | None = Header(None)):
    with conn() as db:
        t = db.execute('SELECT * FROM teams WHERE id=?',(team_id,)).fetchone()
        if not t:
            raise HTTPException(404,'Team not found')
        host(authorization,t['game_id'])
        values = data.model_dump(exclude_none=True)
        if 'clients' in values and values['clients']<0:
            raise HTTPException(422,'Clients cannot be negative')
        for k,v in values.items():
            db.execute(f'UPDATE teams SET {k}=? WHERE id=?',(v,team_id))
        return dict(db.execute('SELECT * FROM teams WHERE id=?',(team_id,)).fetchone())

@app.post('/games/{game_id}/advance')
def advance(game_id: str, authorization: str | None = Header(None)):
    host(authorization,game_id)
    with conn() as db:
        g = game(db,game_id)
        if not g['started'] or (g['act']==7 and g['step']==len(STEPS)-1):
            raise HTTPException(409,'Game not active or at final step')
        pending = db.execute("SELECT COUNT(*) FROM decisions d JOIN proposals p ON p.id=d.proposal_id JOIN teams t ON t.id=p.team_id WHERE t.game_id=? AND p.act=? AND p.step=? AND d.status='approved'",(game_id,g['act'],g['step'])).fetchone()[0]
        if pending:
            raise HTTPException(409,f'Unconfirmed decisions: {pending}')
        act,step = g['act'],g['step']+1
        if step==len(STEPS):
            act,step = act+1,0
            db.execute('UPDATE teams SET points=points+130 WHERE game_id=?',(game_id,))
        db.execute('UPDATE games SET act=?,step=? WHERE id=?',(act,step,game_id))
    return {'act':act,'step':STEPS[step]}
