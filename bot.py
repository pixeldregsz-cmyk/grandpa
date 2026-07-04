#!/usr/bin/env python3
"""AI Telegram Bot."""

import os,json,logging,sqlite3,subprocess,urllib.request,urllib.parse
from pathlib import Path
from datetime import datetime,timezone
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder,CommandHandler,MessageHandler,ContextTypes,filters

TELEGRAM_TOKEN=os.environ.get("TELEGRAM_TOKEN","")
OPENAI_API_KEY=os.environ.get("OPENAI_API_KEY","ogw_live_ca8d7717e42b7f665c5c919205b42aa0")
OPENAI_BASE_URL=os.environ.get("OPENAI_BASE_URL","https://opengateway.gitlawb.com/v1")
MODEL=os.environ.get("MODEL","mimo-v2.5-pro")
SOUL_PATH=Path(__file__).parent/"soul.md"
DATA_DIR=Path(os.environ.get("DATA_DIR","/data"))
DB_PATH=DATA_DIR/"memory.db"
WORKSPACE=Path(os.environ.get("WORKSPACE","/data/workspace"))
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",level=logging.INFO)
logger=logging.getLogger(__name__)
SYSTEM_PROMPT=SOUL_PATH.read_text() if SOUL_PATH.exists() else "You are a helpful AI assistant."
client=OpenAI(api_key=OPENAI_API_KEY,base_url=OPENAI_BASE_URL)
MAX_HISTORY=40

def init_db():
    DATA_DIR.mkdir(parents=True,exist_ok=True);WORKSPACE.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(DB_PATH))
    conn.execute("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,timestamp TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS user_profiles (chat_id INTEGER PRIMARY KEY,username TEXT,first_seen TEXT,last_seen TEXT,notes TEXT)")
    conn.commit();conn.close()

def get_history(cid):
    conn=sqlite3.connect(str(DB_PATH))
    rows=conn.execute("SELECT role,content FROM conversations WHERE chat_id=? ORDER BY id DESC LIMIT ?",(cid,MAX_HISTORY)).fetchall()
    conn.close()
    return[{"role":r,"content":c}for r,c in reversed(rows)]

def add_message(cid,role,content,username=None):
    conn=sqlite3.connect(str(DB_PATH));now=datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO conversations (chat_id,role,content,timestamp) VALUES(?,?,?,?)",(cid,role,content,now))
    conn.execute("DELETE FROM conversations WHERE chat_id=? AND id NOT IN(SELECT id FROM conversations WHERE chat_id=? ORDER BY id DESC LIMIT ?)",(cid,cid,MAX_HISTORY))
    if username:conn.execute("INSERT INTO user_profiles (chat_id,username,first_seen,last_seen) VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET last_seen=?,username=?",(cid,username,now,now,now,username))
    conn.commit();conn.close()

def get_user_notes(cid):
    conn=sqlite3.connect(str(DB_PATH));row=conn.execute("SELECT notes FROM user_profiles WHERE chat_id=?",(cid,)).fetchone();conn.close()
    return row[0] if row and row[0] else""

def save_user_note(cid,note):
    conn=sqlite3.connect(str(DB_PATH))
    ex=conn.execute("SELECT notes FROM user_profiles WHERE chat_id=?",(cid,)).fetchone()
    old=ex[0] if ex and ex[0] else""
    new=f"{old}\n- {note}".strip() if old else f"- {note}"
    conn.execute("INSERT INTO user_profiles (chat_id,notes) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET notes=?",(cid,new,new))
    conn.commit();conn.close()

TOOLS=[{"type":"function","function":{"name":"run_shell","description":"Execute shell command.","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}},{"type":"function","function":{"name":"run_python","description":"Execute Python code.","parameters":{"type":"object","properties":{"code":{"type":"string"}},"required":["code"]}}},{"type":"function","function":{"name":"http_request","description":"Make HTTP request.","parameters":{"type":"object","properties":{"url":{"type":"string"},"method":{"type":"string","default":"GET"},"headers":{"type":"object"},"body":{"type":"string"}},"required":["url"]}}},{"type":"function","function":{"name":"web_search","description":"Search web.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},{"type":"function","function":{"name":"save_file","description":"Save file.","parameters":{"type":"object","properties":{"filename":{"type":"string"},"content":{"type":"string"}},"required":["filename","content"]}}},{"type":"function","function":{"name":"read_file","description":"Read file.","parameters":{"type":"object","properties":{"filepath":{"type":"string"}},"required":["filepath"]}}},{"type":"function","function":{"name":"list_files","description":"List files.","parameters":{"type":"object","properties":{"path":{"type":"string","default":"."}}}}},{"type":"function","function":{"name":"send_file_to_user","description":"Send file via Telegram.","parameters":{"type":"object","properties":{"filepath":{"type":"string"},"caption":{"type":"string"}},"required":["filepath"]}}},{"type":"function","function":{"name":"download_file","description":"Download file.","parameters":{"type":"object","properties":{"url":{"type":"string"},"filename":{"type":"string"}},"required":["url","filename"]}}}]

def run_tool(name,args):
    try:
        if name=="run_shell":r=subprocess.run(args["command"],shell=True,capture_output=True,text=True,timeout=60,cwd=str(WORKSPACE),env={**os.environ,"HOME":str(WORKSPACE)});return(r.stdout+r.stderr)[:5000]or f"[exit {r.returncode}]"
        elif name=="run_python":r=subprocess.run(["python3","-c",args["code"]],capture_output=True,text=True,timeout=60,cwd=str(WORKSPACE));return(r.stdout+r.stderr)[:5000]or"(no output)"
        elif name=="http_request":req=urllib.request.Request(args["url"],method=args.get("method","GET").upper());req.add_header("User-Agent","AIBot/1.0");[req.add_header(k,v)for k,v in(args.get("headers")or{}).items()];req.data=args["body"].encode()if args.get("body")else None;resp=urllib.request.urlopen(req,timeout=15);return f"[HTTP {resp.status}]\n{resp.read(5000).decode(errors=\"replace\")}"
        elif name=="web_search":url=f"https://api.duckduckgo.com/?q={urllib.parse.quote(args['query'])}&format=json&no_html=1";resp=urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"AIBot"}),timeout=10);d=json.loads(resp.read());r=[];[r.append(d["AbstractText"])if d.get("AbstractText")else None];[r.append("- "+t["Text"])for t in d.get("RelatedTopics",[])[:5]if t.get("Text")];return"\n".join(r)or"No results."
        elif name=="save_file":p=WORKSPACE/args["filename"];p.parent.mkdir(parents=True,exist_ok=True);p.write_text(args["content"]);return f"Saved {args['filename']}"
        elif name=="read_file":p=Path(args["filepath"])if args["filepath"].startswith("/")else WORKSPACE/args["filepath"];return p.read_text()[:5000]if p.exists()else"Not found"
        elif name=="list_files":p=Path(args.get("path","."))if args.get("path",".").startswith("/")else WORKSPACE/args.get("path",".");return"\n".join(f"{'[DIR]' if f.is_dir() else 'FILE'} {f.name}"for f in sorted(p.iterdir()))if p.exists()and p.is_dir()else"Empty"
        elif name=="send_file_to_user":p=Path(args["filepath"])if args["filepath"].startswith("/")else WORKSPACE/args["filepath"];return f"__SEND_FILE__{p}__{args.get('caption',')}"if p.exists()else"Not found"
        elif name=="download_file":p=WORKSPACE/args["filename"];p.parent.mkdir(parents=True,exist_ok=True);resp=urllib.request.urlopen(urllib.request.Request(args["url"],headers={"User-Agent":"AIBot"}),timeout=30);p.write_bytes(resp.read());return f"Downloaded {args['filename']}"
        return"Unknown tool"
    except Exception as e:return f"Error: {e}"

def agent_respond(sys_prompt,messages):
    files=[]
    for _ in range(8):
        resp=client.chat.completions.create(model=MODEL,messages=messages,tools=TOOLS,max_tokens=2048,temperature=0.85)
        msg=resp.choices[0].message
        if not msg.tool_calls:return msg.content or"",files
        messages.append(msg)
        for tc in msg.tool_calls:
            try:args=json.loads(tc.function.arguments)
            except:args={}
            result=run_tool(tc.function.name,args)
            if result.startswith("__SEND_FILE__"):parts=result.replace("__SEND_FILE__","").split("__",1);files.append({"path":parts[0],"caption":parts[1]if len(parts)>1 else""});result="File queued."
            messages.append({"role":"tool","tool_call_id":tc.id,"content":result})
    return"Thinking too long.",files

async def start(u,c):
    add_message(u.effective_chat.id,"system","User started.",u.effective_user.username)
    await u.message.reply_text("*Bot online.*\n\nI run commands, search web, execute code, manage files.\n\n/start /reset /remember",parse_mode="Markdown")

async def reset(u,c):
    conn=sqlite3.connect(str(DB_PATH));conn.execute("DELETE FROM conversations WHERE chat_id=?",(u.effective_chat.id,));conn.commit();conn.close()
    await u.message.reply_text("Memory cleared.")

async def remember(u,c):
    note=u.message.text.replace("/remember","").strip()
    if not note:await u.message.reply_text("Usage: /remember something");return
    save_user_note(u.effective_chat.id,note);await u.message.reply_text("Got it!")

async def chat(u,c):
    if not OPENAI_API_KEY:await u.message.reply_text("No API key.");return
    cid=u.effective_chat.id;un=u.effective_user.username or u.effective_user.first_name
    add_message(cid,"user",u.message.text,un)
    sys=SYSTEM_PROMPT+"\n\n## Current Time\n"+datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sys+="\n\n## Tools\nFULL SYSTEM ACCESS: run_shell,run_python,http_request,web_search,save_file,read_file,list_files,send_file_to_user,download_file. You are a FULL AGENT. Use tools proactively."
    notes=get_user_notes(cid)
    if notes:sys+=f"\n\n## User Notes\n{notes}"
    messages=[{"role":"system","content":sys}]+get_history(cid)
    try:
        reply,files=agent_respond(sys,messages)
        add_message(cid,"assistant",reply,un)
        if reply:
            for i in range(0,len(reply),4096):await u.message.reply_text(reply[i:i+4096],parse_mode="Markdown")
        for f in files:
            try:
                with open(f["path"],"rb")as fh:await u.message.reply_document(document=fh,caption=f.get("caption")or None)
            except Exception as e:await u.message.reply_text(f"Couldn't send file: {e}")
    except Exception as e:logger.error(f"Error: {e}");await u.message.reply_text("Something went wrong.")

def main():
    if not TELEGRAM_TOKEN:print("ERROR: Set TELEGRAM_TOKEN");return
    init_db();print(f"Bot starting (model: {MODEL})...")
    app=ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",start));app.add_handler(CommandHandler("reset",reset));app.add_handler(CommandHandler("remember",remember))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,chat));app.run_polling()

if __name__=="__main__":main()