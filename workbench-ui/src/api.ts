export type Kind = 'draft'|'prd'|'plan'|'tasks'|'teardown'
export type DocumentKind = Kind|'note'
export type Summary = { id:string; version:number; prd_id:string|null; created_at:string; stale?:boolean }
export type Project = { id:string; name:string; idea:string; stage:string; created_at:string; updated_at:string; documents:Record<Kind,Summary|null>; active_job:Job|null }
export type Doc = Summary & { current?:boolean; project_id:string|null; kind:DocumentKind; content:string; metadata:{reply?:string;questions?:string[];ready?:boolean;change_note?:string;source?:string;draft_id?:string|null;[key:string]:unknown} }
export type Task = { id:string; project_id:string; document_id:string; title:string; description:string; acceptance:string; priority:string; status:'todo'|'doing'|'done'|'blocked'; updated_at:string }
export type Job = { id:string; project_id:string; kind:string; status:string; error:string|null; created_at:string; updated_at:string; usage:Record<string,number|string|boolean|null>; result:{document_id:string}|null }
export type Message = { seq:number; role:string; content:string; created_at:string }
export type Model = { configured:boolean; model:string; runtime:string; runtime_version:string; validation:string }
export type Resources = { docs:Doc[]; tasks:Task[]; stale:boolean; jobs:Job[]; messages:Message[] }
export const empty:Resources = {docs:[],tasks:[],stale:false,jobs:[],messages:[]}
export const kinds:Record<DocumentKind,string> = {draft:'需求草稿',prd:'产品需求',plan:'技术方案',tasks:'开发任务',teardown:'产品拆解',note:'讨论结论'}
export const states = {todo:'待开始',doing:'进行中',done:'已完成',blocked:'待解决'}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch('/api'+path,{method,headers:body!==undefined?{'Content-Type':'application/json'}:{},body:body===undefined?undefined:JSON.stringify(body)})
  const data=await response.json()
  if(!response.ok) throw new Error(data.error?.message||'操作未完成，请稍后重试')
  return data as T
}
export async function resources(id:string):Promise<Resources>{
  const [docs,tasks,jobs,messages]=await Promise.all([
    api<{items:Doc[]}>(`/projects/${id}/documents`),api<{items:Task[];stale:boolean}>(`/projects/${id}/tasks`),
    api<{items:Job[]}>(`/projects/${id}/jobs`),api<{items:Message[]}>(`/projects/${id}/messages`)
  ])
  return {docs:docs.items,tasks:tasks.items,stale:tasks.stale,jobs:jobs.items,messages:messages.items}
}
export function download(text:string,name:string){
  const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}))
  const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
