"use strict";
const conversation = document.getElementById("conversation");
const form = document.getElementById("chat-form");
const input = document.getElementById("message");
const send = document.getElementById("send");
const reset = document.getElementById("reset");
let sessionId = crypto.randomUUID();
let busy = false;
function node(tag, className, text) {const el=document.createElement(tag); if(className) el.className=className; if(text!==undefined) el.textContent=text; return el;}
function scrollDown(){conversation.scrollTop=conversation.scrollHeight;}
function bubble(role, text){const box=node("div","bubble "+role);if(role==="agent")box.append(node("div","speaker","TRAVEL NEGOTIATOR"));box.append(node("div","",text));return box;}
function render(reply){const section=node("div","agent-block");section.append(bubble("agent",reply.message));
if(reply.options?.length){const grid=node("div","result-grid");reply.options.forEach((o,i)=>{const card=node("div","offer");card.append(node("div","destination",o.destination+" · ВАРИАНТ "+(i+1)));card.append(node("h4","",o.hotel));card.append(node("div","price",o.price));card.append(node("div","detail",[o.date,o.duration,o.stars,o.meal,o.flight,o.pets].join(" · ")));card.append(node("div","badge","Демо · без реального бронирования"));grid.append(card);});section.append(grid);}
if(reply.compromises?.length){reply.compromises.forEach((c,i)=>{const box=node("div","compromise");box.append(node("strong","","Компромисс "+(i+1)+" · "+c.price));box.append(node("div","",c.description));section.append(box);});}
if(reply.commands?.length){const actions=node("div","actions");reply.commands.forEach(c=>{const button=node("button","action",c.label);button.type="button";button.disabled=busy;button.addEventListener("click",()=>submit(c.message));actions.append(button);});section.append(actions);}
conversation.append(section);scrollDown();}
async function submit(message){if(busy)return;const text=message.trim();if(!text)return;busy=true;send.disabled=true;input.disabled=true;conversation.querySelector(".welcome")?.remove();conversation.append(bubble("user",text));const waiting=bubble("agent","Подбираю варианты…");conversation.append(waiting);scrollDown();try{const response=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_id:sessionId,message:text})});const result=await response.json();waiting.remove();if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:"Ошибка API");render(result);}catch(error){waiting.remove();conversation.append(bubble("agent","Ошибка: "+error.message));}finally{busy=false;send.disabled=false;input.disabled=false;input.value="";input.focus();scrollDown();}}
form.addEventListener("submit",e=>{e.preventDefault();submit(input.value);});reset.addEventListener("click",()=>{if(busy)return;sessionId=crypto.randomUUID();conversation.replaceChildren();const intro=node("div","welcome");intro.append(node("h3","","Новый диалог"));intro.append(node("p","","Опишите поездку, и я помогу подобрать варианты."));conversation.append(intro);input.focus();});
