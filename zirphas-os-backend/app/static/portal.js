"use strict";
const token = location.pathname.split("/").pop();
const endpoint = `/api/v1/public/status/${encodeURIComponent(token)}`;
const el = id => document.getElementById(id);
const currency = value => Number(value).toLocaleString("pt-BR", {style:"currency", currency:"BRL"});
const steps = ["intake","analyzing","awaiting_approval","repairing","completed","delivered"];
const messages = {
  intake:["Recebemos seu aparelho", "Nossa equipe vai conferir o aparelho e iniciar a análise técnica."],
  analyzing:["Estamos analisando", "O técnico está preparando o diagnóstico. Quando o orçamento estiver pronto, você poderá decidir por aqui."],
  awaiting_approval:["A decisão é sua", "Confira as peças, a mão de obra e o diagnóstico. O reparo só começa com sua aprovação."],
  awaiting_parts:["Aguardando uma peça", "Seu orçamento foi aprovado. Estamos aguardando estoque para continuar o reparo."],
  repairing:["Cuidando do seu aparelho", "Orçamento aprovado e reparo em andamento. Acompanhe as próximas atualizações aqui."],
  completed:["Reparo concluído", "Seu aparelho está pronto. Combine a retirada e o pagamento com a recepção."],
  delivered:["Tudo certo por aqui", "Aparelho entregue e pagamento registrado. Obrigado pela confiança!"]
};
let current = null;
let busy = false;
let loading = false;
function render(order) {
  current = order;
  el("order").hidden = false;
  el("order-number").textContent = `ORDEM DE SERVIÇO #${String(order.id).padStart(5,"0")}`;
  el("device").textContent = order.device;
  el("status").textContent = order.status_label;
  const [title, body] = messages[order.status];
  el("next-title").textContent = title;
  el("next-body").textContent = body;
  el("updated").textContent = `Última mudança: ${new Date(order.updated_at).toLocaleString("pt-BR")}`;
  const position = steps.indexOf(order.status === "awaiting_parts" ? "repairing" : order.status);
  document.querySelectorAll(".timeline li").forEach((item,index) => {
    item.classList.toggle("done",index < position);
    item.classList.toggle("current",index === position);
    if(index === position) item.setAttribute("aria-current","step"); else item.removeAttribute("aria-current");
  });
  const draft = ["intake","analyzing"].includes(order.status);
  el("draft").textContent = draft ? "O orçamento aparecerá aqui após a análise técnica." : "";
  el("quote-values").hidden = draft;
  el("labor").textContent = currency(order.labor_amount);
  el("total").textContent = currency(order.total_amount);
  el("report").textContent = order.technical_report || "";
  el("quote-version").textContent = `Orçamento · versão ${order.quote_version}`;
  el("parts").replaceChildren(...order.parts.map(part => {
    const row=document.createElement("li"), name=document.createElement("span"), price=document.createElement("strong");
    name.textContent = `${part.quantity} × ${part.name}`;
    price.textContent = currency(Number(part.unit_price)*part.quantity);
    row.append(name,price); return row;
  }));
  el("actions").hidden = !order.can_decide;
  el("approved-note").textContent = order.approved_at ? "✓ Orçamento aprovado" : "";
}
async function refresh() {
  if(busy || loading) return;
  loading = true;
  try {
    const response = await fetch(endpoint,{cache:"no-store"});
    if(!response.ok) {
      if(response.status === 404) { el("order").hidden = true; current=null; }
      throw new Error(response.status === 404 ? "Este link não está disponível. Solicite um novo link à recepção." : "Não foi possível atualizar. Tentaremos novamente em alguns segundos.");
    }
    const data = await response.json();
    if(!busy) { render(data); el("message").textContent=""; }
  } catch(error) { el("message").textContent=error.message; }
  finally { loading=false; }
}
async function decide(approved) {
  if(busy || loading || !current?.can_decide) return;
  busy = true;
  document.querySelectorAll("button").forEach(button => button.disabled=true);
  try {
    const response = await fetch(`${endpoint}/decision`, {method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({approved,quote_version:current.quote_version})});
    const data = await response.json();
    if(!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Não foi possível registrar sua decisão.");
    render(data);
    el("message").textContent=approved ? "Aprovação registrada. Vamos cuidar do seu aparelho." : "Recusa registrada. A equipe revisará o orçamento.";
  } catch(error) { el("message").textContent=error.message; }
  finally { busy=false; document.querySelectorAll("button").forEach(button => button.disabled=false); }
}
el("approve").addEventListener("click",()=>decide(true));
el("decline").addEventListener("click",()=>decide(false));
refresh();
setInterval(refresh,10000);
