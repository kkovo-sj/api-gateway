"""
ChatGPT 式聊天页面 + 模型测速
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["chat-ui"])

CHAT_HTML = r'''<!DOCTYPE html>
<html lang="zh" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Chat — KK API</title>
<style>
:root{--bg:#0a0a0a;--bg2:#141414;--bg3:#1e1e1e;--text:#eee;--text2:#888;--border:#2a2a2a;--accent:#fff;--radius:10px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;-webkit-font-smoothing:antialiased}
.topbar{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--bg2);flex-shrink:0}
.topbar select{background:var(--bg3);color:var(--text);border:1px solid var(--border);padding:6px 12px;border-radius:6px;font-size:13px;outline:none}
.topbar a{color:var(--text2);text-decoration:none;font-size:13px}
.topbar a:hover{color:var(--text)}
.messages{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:16px}
.msg{display:flex;gap:12px;max-width:820px;width:100%;margin:0 auto;animation:fadeIn .3s}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.msg .avatar{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.msg.user .avatar{background:#333}
.msg.assistant .avatar{background:#1a1a1a;border:1px solid var(--border)}
.msg .content{flex:1;min-width:0;font-size:14px;line-height:1.7}
.msg .content p{margin-bottom:8px}
.msg .content pre{background:var(--bg3);padding:12px 16px;border-radius:6px;overflow-x:auto;font-size:13px;margin:8px 0;border:1px solid var(--border)}
.msg .content code{font-family:'SF Mono','Fira Code',monospace;font-size:13px;background:var(--bg3);padding:2px 5px;border-radius:3px}
.msg .content pre code{background:none;padding:0}
.msg.user .content{color:var(--text)}
.msg.assistant .content{color:var(--text2)}
.msg .meta{font-size:10px;color:var(--text2);margin-top:4px}
.input-area{display:flex;gap:10px;padding:14px 20px;border-top:1px solid var(--border);background:var(--bg2);flex-shrink:0;max-width:860px;width:100%;margin:0 auto}
.input-area textarea{flex:1;background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;font-size:14px;resize:none;outline:none;font-family:inherit;rows:1;max-height:120px}
.input-area textarea:focus{border-color:var(--accent)}
.input-area button{background:var(--accent);color:var(--bg);border:none;border-radius:var(--radius);padding:0 20px;font-size:14px;font-weight:600;cursor:pointer;transition:opacity .2s}
.input-area button:hover{opacity:.85}
.input-area button:disabled{opacity:.4;cursor:default}
.typing{display:flex;gap:4px;padding:4px 0}
.typing span{width:6px;height:6px;border-radius:50%;background:var(--text2);animation:typing 1.4s infinite}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes typing{0%,60%,100%{opacity:.2}30%{opacity:1}}
.toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:0 20px 10px;max-width:860px;margin:0 auto;width:100%}
.toolbar button{background:var(--bg3);border:1px solid var(--border);color:var(--text2);padding:5px 10px;border-radius:5px;font-size:11px;cursor:pointer}
.toolbar button:hover{color:var(--text)}
@media(max-width:768px){.messages{padding:12px}.input-area{padding:10px 12px}}
</style>
</head>
<body>

<div class="topbar">
  <a href="/">← 首页</a>
  <select id="modelSelect" onchange="switchModel()">
    <option value="gpt-5.5">GPT-5.5</option>
    <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
    <option value="claude-opus-4-8">Claude Opus 4.8</option>
    <option value="deepseek-chat">DeepSeek V3</option>
    <option value="deepseek-r1">DeepSeek R1</option>
    <option value="qwen-turbo">Qwen Turbo</option>
    <option value="grok-4.3">Grok 4.3</option>
  </select>
  <span style="font-size:11px;color:var(--text2);margin-left:auto" id="status"></span>
</div>

<div class="messages" id="messages">
  <div class="msg assistant">
    <div class="avatar">🤖</div>
    <div class="content">
      <p>你好！选择一个模型，开始对话。</p>
      <p style="font-size:12px;color:var(--text2)">支持 GPT-5.5 · Claude · DeepSeek · Qwen · Grok</p>
    </div>
  </div>
</div>

<div class="toolbar">
  <button onclick="clearChat()">🗑 清空</button>
  <button onclick="copyChat()">📋 复制对话</button>
  <span style="font-size:10px;color:var(--text2);margin-left:auto" id="tokenCount"></span>
</div>

<div class="input-area">
  <textarea id="input" placeholder="输入消息... (Enter发送, Shift+Enter换行)" rows="1" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"></textarea>
  <button id="sendBtn" onclick="send()">发送</button>
</div>

<script>
const API='/v1/chat/completions'
let model='gpt-5.5',messages=[],totalTokens=0,apiKey=''

// 尝试从 localstorage 读取 key，没有就提示
apiKey=localStorage.getItem('kk_api_key')||''
if(!apiKey){
  apiKey=prompt('请输入你的 API Key（去门户获取）:','')
  if(apiKey)localStorage.setItem('kk_api_key',apiKey)
}

function switchModel(){model=document.getElementById('modelSelect').value}
function clearChat(){messages=[];totalTokens=0;document.getElementById('messages').innerHTML='';document.getElementById('tokenCount').textContent=''}

function addMessage(role,content,modelName){
  messages.push({role,content})
  const div=document.createElement('div')
  div.className='msg '+role
  const avatar=role==='user'?'👤':'🤖'
  const meta=role==='assistant'?`<div class="meta">${modelName||model} · ${new Date().toLocaleTimeString()}</div>`:''
  div.innerHTML=`<div class="avatar">${avatar}</div><div class="content">${formatContent(content)}${meta}</div>`
  document.getElementById('messages').appendChild(div)
  document.getElementById('messages').scrollTop=document.getElementById('messages').scrollHeight
}

function formatContent(text){
  // Simple markdown
  return text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/```(\w*)\n([\s\S]*?)```/g,'<pre><code>$2</code></pre>')
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .replace(/\n/g,'<br>')
}

async function send(){
  const input=document.getElementById('input')
  const btn=document.getElementById('sendBtn')
  const text=input.value.trim()
  if(!text||!apiKey)return
  input.value='';btn.disabled=true

  addMessage('user',text)
  // Add typing indicator
  const typingDiv=document.createElement('div')
  typingDiv.className='msg assistant'
  typingDiv.innerHTML='<div class="avatar">🤖</div><div class="content"><div class="typing"><span></span><span></span><span></span></div></div>'
  document.getElementById('messages').appendChild(typingDiv)
  document.getElementById('messages').scrollTop=document.getElementById('messages').scrollHeight

  try{
    const r=await fetch(API,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+apiKey},body:JSON.stringify({model,messages:[...messages,{role:'user',content:text}],max_tokens:2000})})
    const d=await r.json()
    typingDiv.remove()

    if(d.choices){
      const reply=d.choices[0].message.content
      addMessage('assistant',reply,d.model)
      totalTokens+=(d.usage?.total_tokens||0)
      document.getElementById('tokenCount').textContent='消耗 '+totalTokens.toLocaleString()+' tokens'
    }else if(d.error){
      addMessage('assistant','❌ '+d.error.message)
    }
  }catch(e){
    typingDiv.remove()
    addMessage('assistant','❌ 网络错误: '+e.message)
  }
  btn.disabled=false;input.focus()
}

function copyChat(){
  const msgs=document.querySelectorAll('.msg .content')
  let text=''
  msgs.forEach(m=>text+=m.innerText+'\n\n')
  navigator.clipboard.writeText(text)
  document.getElementById('status').textContent='已复制!'
  setTimeout(()=>document.getElementById('status').textContent='',2000)
}
</script>
</body>
</html>'''

@router.get("/chat", response_class=HTMLResponse)
async def chat_page():
    return CHAT_HTML
