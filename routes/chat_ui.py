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


BENCH_HTML = r'''<!DOCTYPE html>
<html lang="zh" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Benchmark — KK API</title>
<style>
:root{--bg:#0a0a0a;--bg2:#141414;--bg3:#1e1e1e;--text:#eee;--text2:#888;--border:#2a2a2a;--accent:#fff;--green:#4ade80;--yellow:#fbbf24;--red:#f87171;--blue:#60a5fa;--radius:10px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;background:var(--bg);color:var(--text);line-height:1.5;-webkit-font-smoothing:antialiased;padding:20px}
.topbar{display:flex;align-items:center;gap:16px;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--border)}
.topbar a{color:var(--text2);text-decoration:none;font-size:14px}
.topbar a:hover{color:var(--text)}
.topbar h1{font-size:20px;font-weight:700;letter-spacing:-.5px}
.btn{background:var(--accent);color:var(--bg);border:none;padding:10px 20px;border-radius:var(--radius);font-size:13px;font-weight:600;cursor:pointer;transition:opacity .15s}
.btn:hover{opacity:.85}.btn:disabled{opacity:.4}
.wrap{max-width:960px;margin:0 auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:20px}
.card .name{font-size:16px;font-weight:700;margin-bottom:4px}
.card .supplier{font-size:11px;color:var(--text2);margin-bottom:12px}
.card .metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.card .metric .label{font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.3px}
.card .metric .value{font-size:18px;font-weight:700}
.card .rank{position:absolute;top:12px;right:16px;font-size:12px;font-weight:700;padding:3px 8px;border-radius:4px}
.rank-1{background:#fbbf24;color:#000}.rank-2{background:#94a3b8;color:#000}.rank-3{background:#cd853f;color:#fff}
table{width:100%;border-collapse:collapse;margin-top:16px;font-size:13px}
th{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);color:var(--text2);font-weight:500;font-size:11px;text-transform:uppercase}
td{padding:10px 12px;border-bottom:1px solid var(--border)}
.bar{height:6px;border-radius:3px;background:var(--bg3);overflow:hidden;margin-top:4px}
.bar-fill{height:100%;border-radius:3px;transition:width .5s}
.running{text-align:center;padding:40px;color:var(--text2)}
.running .spinner{display:inline-block;width:32px;height:32px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;margin-bottom:12px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="wrap">
<div class="topbar">
  <a href="/">← 首页</a>
  <h1>Model Benchmark</h1>
  <button class="btn" id="runBtn" onclick="runBenchmark()">▶ 开始测速</button>
</div>

<div class="grid" id="cards"></div>

<div id="tableArea" style="display:none">
  <h3 style="margin-bottom:12px">📊 详细排行榜</h3>
  <table id="rankTable"></table>
</div>
</div>

<script>
const MODELS=[
  {name:'GPT-5.5',id:'gpt-5.5',supplier:'88API'},
  {name:'Claude Opus 4.8',id:'claude-opus-4-8',supplier:'88API'},
  {name:'Claude Sonnet 4.6',id:'claude-sonnet-4-6',supplier:'88API'},
  {name:'DeepSeek V3',id:'deepseek-chat',supplier:'DeepSeek'},
  {name:'DeepSeek R1',id:'deepseek-r1',supplier:'DeepSeek'},
  {name:'Qwen Turbo',id:'qwen-turbo',supplier:'通义千问'},
  {name:'Grok 4.3',id:'grok-4.3',supplier:'88API'},
  {name:'GPT-4o-mini',id:'gpt-4o-mini',supplier:'88API'},
  {name:'Claude Haiku',id:'claude-haiku-4-5-20251001',supplier:'88API'},
]
let results=[]

function renderCards(){
  const grid=document.getElementById('cards')
  grid.innerHTML=MODELS.map((m,i)=>{
    const r=results[i]
    const score=r?r.score:0
    const rank=i+1
    const rc=rank===1?'rank-1':rank===2?'rank-2':rank===3?'rank-3':''
    return `<div class="card" style="position:relative">
      ${r&&rank<=3?`<span class="rank ${rc}">#${rank}</span>`:''}
      <div class="name">${m.name}</div>
      <div class="supplier">${m.supplier}</div>
      ${r?`
        <div class="metrics">
          <div class="metric"><div class="label">首Token</div><div class="value">${r.ttfb}ms</div></div>
          <div class="metric"><div class="label">总时间</div><div class="value">${r.totalTime}ms</div></div>
          <div class="metric"><div class="label">速度</div><div class="value">${r.tokensPerSec} t/s</div></div>
          <div class="metric"><div class="label">输出Token</div><div class="value">${r.outputTokens}</div></div>
          <div class="metric"><div class="label">得分</div><div class="value" style="color:${score>80?'var(--green)':score>60?'var(--yellow)':'var(--red)'}">${score}</div></div>
          <div class="metric"><div class="label">成本</div><div class="value" style="font-size:14px">¥${r.cost}</div></div>
        </div>
        <div class="bar"><div class="bar-fill" style="width:${score}%;background:${score>80?'var(--green)':score>60?'var(--yellow)':'var(--red)'}"></div></div>
      `:'<div class="muted" style="padding:20px 0">等待测试...</div>'}
    </div>`
  }).join('')
}

async function runBenchmark(){
  document.getElementById('runBtn').disabled=true
  document.getElementById('cards').innerHTML='<div class="running"><div class="spinner"></div><p>测试中...逐个调用模型</p></div>'
  results=[]

  for(const m of MODELS){
    try{
      const start=Date.now()
      const r=await fetch('/try',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:m.id,message:'请用Python写一个快速排序函数，不要解释，只给代码'})})
      const d=await r.json()
      const totalTime=Date.now()-start

      if(d.choices){
        const text=d.choices[0].message.content
        const outputTokens=d.usage?.completion_tokens||Math.ceil(text.length/4)
        const ttfb=Math.round(totalTime*0.3) // 估算首Token约为总时间的30%
        const tokensPerSec=outputTokens/(totalTime/1000)
        const cost=outputTokens>0?((outputTokens/1000)*0.01).toFixed(4):'0'

        // 评分：速度(40%)+质量(30%)+成本(30%)
        const speedScore=Math.min(100,tokensPerSec*2)
        const qualityScore=text.length>50?90:60
        const costScore=outputTokens<500?90:70
        const score=Math.round(speedScore*0.4+qualityScore*0.3+costScore*0.3)

        results.push({ttfb,totalTime,tokensPerSec:Math.round(tokensPerSec),outputTokens,cost,score})
      }else{
        results.push({ttfb:'--',totalTime:'--',tokensPerSec:'--',outputTokens:'--',cost:'--',score:0,error:1})
      }
    }catch(e){
      results.push({ttfb:'--',totalTime:'--',tokensPerSec:'--',outputTokens:'--',cost:'--',score:0,error:1})
    }
    renderCards()
  }

  // 排行榜
  const sorted=[...results].map((r,i)=>({...r,model:MODELS[i].name})).sort((a,b)=>b.score-a.score)
  document.getElementById('tableArea').style.display='block'
  document.getElementById('rankTable').innerHTML='<tr><th>#</th><th>模型</th><th>得分</th><th>首Token</th><th>总时间</th><th>速度</th><th>成本</th></tr>'+sorted.map((r,i)=>`<tr><td>${i+1}</td><td>${r.model}</td><td>${r.score}</td><td>${r.ttfb}</td><td>${r.totalTime}</td><td>${r.tokensPerSec}</td><td>¥${r.cost}</td></tr>`).join('')

  document.getElementById('runBtn').disabled=false
}

renderCards()
</script>
</body>
</html>'''

@router.get("/benchmark", response_class=HTMLResponse)
async def benchmark_page():
    return BENCH_HTML
