"use strict";
const conversation = document.getElementById("conversation");
const form = document.getElementById("chat-form");
const input = document.getElementById("message");
const send = document.getElementById("send");
const reset = document.getElementById("reset");
const root = document.documentElement;
let sessionId = crypto.randomUUID();
let busy = false;
function node(tag, className, text) {const el=document.createElement(tag); if(className) el.className=className; if(text!==undefined) el.textContent=text; return el;}
function scrollDown(){conversation.scrollTop=conversation.scrollHeight;}
function bubble(role, text){const box=node("div","bubble "+role);if(role==="agent")box.append(node("div","speaker","TRAVEL NEGOTIATOR"));box.append(node("div","",text));return box;}
function render(reply){const section=node("div","agent-block");section.append(bubble("agent",reply.message));
if(reply.options?.length){const grid=node("div","result-grid");reply.options.forEach((o,i)=>{const card=node("div","offer");card.append(node("div","destination",o.destination+" · ВАРИАНТ "+(i+1)));card.append(node("h4","",o.hotel));card.append(node("div","price",o.price));if(Number.isInteger(o.match_score)){card.append(node("div","match-score",`Соответствие запросу: ${o.match_score}/100`));}
if(o.match_reasons?.length){card.append(node("div","match-reasons",o.match_reasons.slice(0,2).join(" · ")));}
if(o.match_caveats?.length){card.append(node("div","match-caveats",o.match_caveats.filter(x=>!x.includes("синтетические")).slice(0,2).join(" · ")));}if(o.price_breakdown)card.append(node("div","detail",o.price_breakdown));card.append(node("div","detail",[o.date,o.duration,o.stars,o.meal,o.flight,o.pets].join(" · ")));card.append(node("div","badge","Демо · без реального бронирования"));
const choose=node("button","action choose-offer",`Выбрать вариант ${i+1}`);
choose.type="button";
choose.dataset.offerId=o.id||"";
choose.dataset.index=String(i+1);
choose.addEventListener("click",()=>{
  if(busy)return;
  choose.textContent="Выбираю вариант…";
  submit(o.id||`выбрать вариант ${i+1}`);
});
card.append(choose);grid.append(card);});section.append(grid);}
if(reply.compromises?.length){reply.compromises.forEach((c,i)=>{const box=node("div","compromise");box.append(node("strong","","Компромисс "+(i+1)+" · "+c.price));box.append(node("div","",c.description));section.append(box);});}
if(reply.commands?.length){const actions=node("div","actions");reply.commands.forEach(c=>{
  if(reply.status==="ready"&&c.label.startsWith("Выбрать вариант"))return;
  const button=node("button","action",c.label);button.type="button";
  button.addEventListener("click",()=>submit(c.message));actions.append(button);
});if(actions.childElementCount)section.append(actions);}
conversation.append(section);scrollDown();}
async function submit(message){
  if(busy)return;const text=message.trim();if(!text)return;
  busy=true;send.disabled=true;input.disabled=true;
  conversation.querySelector(".welcome")?.remove();conversation.append(bubble("user",text));
  const waiting=bubble("agent","Обрабатываю запрос…");conversation.append(waiting);scrollDown();
  try{
    const response=await fetch("/api/chat/stream",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_id:sessionId,message:text})});
    if(!response.ok){let detail="Ошибка API";try{const body=await response.json();detail=body.detail||detail;}catch{}throw new Error(detail);}
    if(!response.body)throw new Error("Streaming не поддерживается браузером");
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="",gotResult=false;
    function processFrame(frame){
      const lines=frame.split("\n");let event="message",data="";
      for(const line of lines){if(line.startsWith("event:"))event=line.slice(6).trim();else if(line.startsWith("data:"))data+=line.slice(5).trimStart();}
      if(!data)return;const value=JSON.parse(data);
      if(event==="progress"){waiting.lastChild.textContent=value.message||"Обрабатываю запрос…";scrollDown();}
      else if(event==="result"){gotResult=true;waiting.remove();render(value);}
      else if(event==="error")throw new Error(value.message||"Ошибка обработки");
    }
    while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});
      const frames=buffer.split(/\r?\n\r?\n/);buffer=frames.pop();for(const frame of frames)processFrame(frame);
    }
    buffer+=decoder.decode();if(buffer.trim())processFrame(buffer);
    if(!gotResult)throw new Error("Поток завершился без результата");
  }catch(error){waiting.remove();conversation.append(bubble("agent","Ошибка: "+error.message));}
  finally{busy=false;send.disabled=false;input.disabled=false;input.value="";input.focus();scrollDown();}
}
form.addEventListener("submit",e=>{e.preventDefault();submit(input.value);});reset.addEventListener("click",()=>{if(busy)return;sessionId=crypto.randomUUID();conversation.replaceChildren();const intro=node("div","welcome");intro.append(node("h3","","Новый диалог"));intro.append(node("p","","Опишите поездку, и я помогу подобрать варианты."));conversation.append(intro);input.focus();});
