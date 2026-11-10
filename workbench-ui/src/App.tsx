import {useCallback,useEffect,useMemo,useRef,useState} from 'react'
import type {ReactNode,FormEvent} from 'react'
import {ArrowDownToLine,ArrowRight,ArrowUpRight,BookOpen,Check,CheckCheck,ChevronDown,ChevronRight,Command,FileText,Folder,FolderOpen,History,Import,LayoutDashboard,ListTodo,LoaderCircle,MessageSquare,MoreHorizontal,Plus,Search,Settings2,ShieldCheck,Sparkles,SquarePen,Workflow,X,PanelLeftClose,Menu,AlertCircle,Circle,CheckCircle2,Send,RefreshCw,Clock3,Leaf,CheckSquare2,ExternalLink} from 'lucide-react'
import {FullSearch,TrashDialog} from './WorkspaceTools'
import type {Hit,TrashTarget} from './WorkspaceTools'
import InstalledSkills from './InstalledSkills'
import Teardown from './Teardown'
import Assistant from './Assistant'
import OriginalTools from './OriginalTools'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {api,resources,empty,kinds,states,download} from './api'
import type {Project,Doc,Task,Job,Model,Resources,Kind} from './api'

type Route={view:string;id?:string;tab?:string;phase?:string}
type Phase={key:string;label:string;tabs:string[]}
const productName='Blue产品经理工作台'
const phases:Phase[]=[
  {key:'teardown',label:'产品拆解',tabs:['teardown']},
  {key:'idea',label:'想法验收',tabs:['interview','prd']},
  {key:'backend',label:'后端整合',tabs:['plan','tasks']},
  {key:'frontend',label:'前端整合',tabs:['frontend']},
  {key:'deploy',label:'开发上线',tabs:['deploy']},
]
const productTabs=[['overview','概览'],['teardown','产品拆解'],['interview','需求访谈'],['prd','PRD 文档'],['plan','技术方案'],['tasks','开发任务'],['history','版本与记录']]
const productTabIcons:Record<string,ReactNode>={overview:<LayoutDashboard size={16}/>,teardown:<Search size={16}/>,interview:<MessageSquare size={16}/>,prd:<FileText size={16}/>,plan:<Workflow size={16}/>,tasks:<CheckSquare2 size={16}/>,history:<History size={16}/>}
const jobKinds:Record<string,string>={interview:'需求梳理',plan:'方案推导',tasks:'任务拆解',teardown:'产品拆解'}
const statusNames:Record<string,string>={queued:'排队中',running:'正在生成',done:'已完成',failed:'未完成',interrupted:'已中断'}
function readRoute():Route{const parts=location.hash.replace(/^#\/?/,'').split('/');return parts[0]==='product'?{view:'product',id:parts[1],tab:parts[2]||'overview'}:parts[0]==='phase'?{view:'phase',phase:parts[1]}:{view:parts[0]||'assistant',id:parts[1],tab:parts[2]}}
function go(view:string,id?:string,tab='overview'){location.hash=view==='product'?`/product/${id}/${tab}`:`/${view}`}
function phaseTab(phase:Phase,p:Project){return phase.key==='idea'?(p.documents.prd?'prd':'interview'):phase.tabs[0]}
function latest(r:Resources,kind:Kind){return r.docs.filter(d=>d.kind===kind&&d.current!==false).sort((a,b)=>b.version-a.version)[0]}
function date(value:string,full=false){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',...(full?{hour:'2-digit',minute:'2-digit'}:{})}).format(new Date(value))}
function docTitle(d:Doc){return d.content.match(/^#\s+(.+)/m)?.[1]?.replace(/\*|#/g,'')||kinds[d.kind]}
function Badge({children,tone='muted'}:{children:ReactNode;tone?:string}){return <span className={`badge ${tone}`}>{children}</span>}
function Empty({icon=<FolderOpen/>,title,description,action}:{icon?:ReactNode;title:string;description:string;action?:ReactNode}){return <div className="empty"><div className="empty-icon">{icon}</div><h3>{title}</h3><p>{description}</p>{action}</div>}
function DocIcon({kind}:{kind:Doc['kind']}){return <span className={`doc-icon ${kind}`}>{kind==='plan'?<Workflow size={18}/>:kind==='tasks'?<CheckSquare2 size={18}/>:kind==='note'?<MessageSquare size={18}/>:<FileText size={18}/>}</span>}
type SavedReference={project_id:string;document_id:string;title:string;version:number}
function RenderDoc({doc,onReference}:{doc:Doc;onReference?:(pid:string,id:string)=>void}){const refs=doc.kind==='note'&&Array.isArray(doc.metadata.references)?doc.metadata.references.filter((x):x is SavedReference=>!!x&&typeof x==='object'&&typeof x.project_id==='string'&&typeof x.document_id==='string'&&typeof x.title==='string'&&typeof x.version==='number'):[];return <><div className="markdown"><Markdown remarkPlugins={[remarkGfm]} components={doc.kind==='note'?{img:({alt})=><span>{alt?`[图片：${alt}]`:'[图片]'}</span>}:undefined}>{doc.content}</Markdown></div>{refs.length>0&&<div className="note-references"><span>保存时引用的资料</span>{refs.map(ref=><button className="text-button" disabled={!onReference} key={ref.document_id} onClick={()=>onReference?.(ref.project_id,ref.document_id)}>{ref.title} · V{ref.version}<ExternalLink size={12}/></button>)}</div>}</>}
function defaultProductTab(p:Project){
  if(p.documents.prd)return 'prd'
  if(p.documents.draft)return 'interview'
  if(p.documents.teardown)return 'teardown'
  return 'interview'
}

export default function App(){
  const [trashOpen,setTrashOpen]=useState(false)
  const [trashTarget,setTrashTarget]=useState<TrashTarget|null>(null)
  const [toolsVersion,setToolsVersion]=useState(0)
  const [route,setRoute]=useState<Route>(readRoute)
  const [projects,setProjects]=useState<Project[]>([])
  const [all,setAll]=useState<Record<string,Resources>>({})
  const [notes,setNotes]=useState<Doc[]>([])
  const [model,setModel]=useState<Model|null>(null)
  const [loading,setLoading]=useState(true)
  const [loadError,setLoadError]=useState('')
  const [query,setQuery]=useState('')
  const [searchExpanded,setSearchExpanded]=useState(false)
  const [toast,setToast]=useState<{text:string;error:boolean}|null>(null)
  const [creating,setCreating]=useState(false)
  const [createName,setCreateName]=useState('')
  const [createIdea,setCreateIdea]=useState('')
  const [busy,setBusy]=useState(false)
  const [sidebar,setSidebar]=useState(false)
  const [reader,setReader]=useState<Doc|null>(null)
  const [editor,setEditor]=useState<{pid:string;content:string;version:number;title:string}|null>(null)
  const [editorNote,setEditorNote]=useState('更新需求')
  const [editingPreview,setEditingPreview]=useState(false)
  const [chat,setChat]=useState('')
  const [taskFilter,setTaskFilter]=useState('all')
  const [taskView,setTaskView]=useState('board')
  const [docFilter,setDocFilter]=useState('all')
  const [docProject,setDocProject]=useState('all')
  const [eventLines,setEventLines]=useState<string[]>([])
  const [expandedTask,setExpandedTask]=useState<string|null>(null)
  const searchRef=useRef<HTMLInputElement>(null)
  const mobileSearchRef=useRef<HTMLInputElement>(null)
  const importRef=useRef<HTMLInputElement>(null)
  const importProject=useRef<string|null>(null)
  const importVersion=useRef(0)
  const refreshLock=useRef(false)
  const notify=(text:string,error=false)=>setToast({text,error})
  const refresh=useCallback(async()=>{
    if(refreshLock.current)return
    refreshLock.current=true
    try{
      const [p,m,n]=await Promise.all([api<{items:Project[]}>('/projects'),api<Model>('/model'),api<{items:Doc[]}>('/notes')])
      const entries=await Promise.all(p.items.map(async project=>[project.id,await resources(project.id)] as const))
      setProjects(p.items);setModel(m);setNotes(n.items);setAll(Object.fromEntries(entries));setLoadError('')
    }catch(e){setLoadError((e as Error).message)}finally{setLoading(false);refreshLock.current=false}
  },[])
  useEffect(()=>{void refresh()},[refresh])
  useEffect(()=>{const onHash=()=>{setRoute(readRoute());setQuery('');setSearchExpanded(false);setSidebar(false);setTaskFilter('all');setExpandedTask(null)};window.addEventListener('hashchange',onHash);return()=>window.removeEventListener('hashchange',onHash)},[])
  const active=projects.some(p=>p.active_job)
  useEffect(()=>{if(!active)return;const timer=setInterval(()=>void refresh(),2500);return()=>clearInterval(timer)},[active,refresh])
  useEffect(()=>{if(!toast)return;const timer=setTimeout(()=>setToast(null),5000);return()=>clearTimeout(timer)},[toast])
  useEffect(()=>{const key=(event:KeyboardEvent)=>{if((event.metaKey||event.ctrlKey)&&event.key==='k'){event.preventDefault();openSearch()}if(event.key==='Escape'){setReader(null);setCreating(false);setEditor(null);setSidebar(false);setSearchExpanded(false)}};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key)},[])
  const project=route.view==='product'?projects.find(p=>p.id===route.id):undefined
  const phase=phases.find(item=>route.view==='phase'?item.key===route.phase:route.view==='product'&&item.tabs.includes(route.tab||''))
  const r=project?all[project.id]||empty:empty
  const currentPrd=latest(r,'prd'),draft=latest(r,'draft'),plan=latest(r,'plan')
  const projectBusy=!!project?.active_job
  const currentJob=project?.active_job
  useEffect(()=>{
    if(!currentJob){setEventLines([]);return}
    let cancelled=false
    const poll=async()=>{try{const response=await api<{items:{message:string}[]}>(`/jobs/${currentJob.id}/events`);if(!cancelled)setEventLines(response.items.map(e=>e.message))}catch{/* 主查询负责连接状态 */}}
    void poll();const timer=setInterval(poll,2500);return()=>{cancelled=true;clearInterval(timer)}
  },[currentJob?.id])
  const allDocs=useMemo(()=>[...Object.values(all).flatMap(x=>x.docs),...notes].sort((a,b)=>b.created_at.localeCompare(a.created_at)),[all,notes])
  const allTasks=useMemo(()=>Object.values(all).flatMap(x=>x.tasks),[all])
  const pending=projects.filter(p=>{const ds=all[p.id];if(!ds)return false;const d=latest(ds,'draft'),pr=latest(ds,'prd');return d&&d.prd_id===(pr?.id||null)&&(!pr||d.created_at>pr.created_at)})
  const filteredProjects=projects.filter(p=>(p.name+p.idea).toLowerCase().includes(query.toLowerCase()))
  const pageNames:Record<string,string>={assistant:'产品助理',overview:'产品助理',workbench:'工作台',projects:'全部产品',documents:'文档库',tasks:'任务中心',originals:'原版工具',settings:'设置'}
  useEffect(()=>{const context=project?.name||phase?.label||pageNames[route.view];document.title=context?`${context} · ${productName}`:productName},[project?.name,phase?.label,route.view])
  function openPhase(next:Phase){if(project)go('product',project.id,phase?.key===next.key&&route.tab?route.tab:phaseTab(next,project));else location.hash=`/phase/${next.key}`}
  function openSearch(){setSearchExpanded(true)}
  function requestTrash(t:TrashTarget){setTrashTarget(t);setTrashOpen(true)}
  async function openHit(hit:Hit){setQuery('');setSearchExpanded(false);if(hit.type==='conversation'){location.hash=`/assistant/${hit.id}/${hit.message_id||''}`;return}if(hit.type==='project'){go('product',hit.id);return}if(hit.type==='task'){go('product',hit.project_id!,'tasks');return}try{setReader(hit.type==='note'?await api<Doc>('/notes/'+hit.id):await api<Doc>(`/projects/${hit.project_id}/documents/${hit.id}`))}catch(e){notify((e as Error).message,true)}}
  async function perform(action:()=>Promise<unknown>,success?:string){setBusy(true);try{await action();await refresh();if(success)notify(success);return true}catch(e){notify((e as Error).message,true);return false}finally{setBusy(false)}}
  async function create(event:FormEvent){event.preventDefault();let id='';const ok=await perform(async()=>{const p=await api<Project>('/projects','POST',{name:createName,idea:createIdea});id=p.id},'产品已创建，可以开始梳理需求');if(ok){setCreating(false);setCreateName('');setCreateIdea('');go('product',id,'interview')}}
  function openCreate(){setCreating(true);setCreateName('');setCreateIdea('')}
  function openEdit(pid:string,content:string,version:number,title:string){setEditor({pid,content,version,title});setEditorNote('更新需求');setEditingPreview(false)}
  function chooseImport(p:Project){importProject.current=p.id;importVersion.current=p.documents.prd?.version||0;importRef.current?.click()}
  async function handleImport(file:File|undefined){if(!file||!importProject.current)return;const pid=importProject.current;if(!file.name.toLowerCase().endsWith('.md')){notify('请选择 Markdown（.md）文档',true);return}if(file.size>400000){notify('文档过大，请控制在 100000 字以内',true);return}const content=await file.text();const ok=await perform(()=>api(`/projects/${pid}/prd/import`,'POST',{filename:file.name,content,expected_version:importVersion.current,change_note:`导入 ${file.name}`}), 'PRD 已导入，历史版本会保留');if(ok)go('product',pid,'prd')}
  async function generate(p:Project,kind:'plan'|'tasks'){const ok=await perform(()=>api(`/projects/${p.id}/${kind==='plan'?'plan':'tasks/generate'}`,'POST',{}),kind==='plan'?'开始推导技术方案':'开始拆解开发任务');if(ok)go('product',p.id,kind)}
  async function send(event:FormEvent){event.preventDefault();if(!project||!chat.trim())return;const ok=await perform(()=>api(`/projects/${project.id}/interview`,'POST',{message:chat}), '已交给 AI 梳理，生成过程中可以切换页面');if(ok)setChat('')}
  async function changeStatus(t:Task,status:Task['status']){await perform(()=>api(`/projects/${t.project_id}/tasks/${t.id}`,'PATCH',{status}), '任务状态已更新')}
  const newButton=<button className="button primary" onClick={openCreate}><Plus size={17}/>新建产品</button>
  const projectName=(id:string|null)=>id?projects.find(p=>p.id===id)?.name||'产品':'未关联产品'
  const visibleDocs=allDocs.filter(d=>(docFilter==='all'||d.kind===docFilter)&&(docProject==='all'||(docProject==='unassigned'?!d.project_id:d.project_id===docProject))&&(docTitle(d)+projectName(d.project_id)).toLowerCase().includes(query.toLowerCase()))
  async function openReferencedDocument(pid:string,id:string){try{setReader(await api<Doc>(`/projects/${pid}/documents/${id}`))}catch(error){notify((error as Error).message,true)}}
  const compactStatus=(p:Project)=>p.active_job?'正在生成':p.stage==='开发跟进'?'任务已拆解':p.stage
  function productList(targetPhase?:Phase){return <section className="simple-home">
    <header className="simple-page-heading"><div><h1>{targetPhase?.label||'全部产品'}</h1>{targetPhase&&<p className="phase-picker-note">选择一个产品继续</p>}</div>{newButton}</header>
    <div className="simple-product-list">{filteredProjects.map(p=><button className="simple-product-row" key={p.id} onClick={()=>go('product',p.id,targetPhase?phaseTab(targetPhase,p):defaultProductTab(p))}>
      <span className="simple-product-symbol"><Folder size={19}/></span><div className="simple-product-copy"><strong>{p.name}</strong><small>{compactStatus(p)} · {date(p.updated_at)} 更新</small></div>{p.active_job?<LoaderCircle className="spin" size={16}/>:<ChevronRight size={17}/>}
    </button>)}</div>
    {!filteredProjects.length&&<div className="simple-empty"><p>{query?'没有匹配的产品':'还没有产品，创建一个开始吧。'}</p>{query&&<button className="text-button" onClick={()=>setQuery('')}>清除搜索</button>}</div>}
  </section>}
  function taskCard(t:Task,withProject=false){return <article className="task-card" key={t.id}>
    <div className="task-card-top"><Badge tone={t.priority==='P0'?'rose':t.priority==='P1'?'amber':'muted'}>{t.priority}</Badge>{withProject&&<button className="text-button task-project" onClick={()=>go('product',t.project_id,'tasks')}>{projectName(t.project_id)}</button>}<button aria-label={`展开任务：${t.title}`} className="icon-button small" onClick={()=>setExpandedTask(expandedTask===t.id?null:t.id)}><MoreHorizontal size={17}/></button></div>
    <h4 onClick={()=>setExpandedTask(expandedTask===t.id?null:t.id)}>{t.title}</h4><p>{t.description}</p>
    {expandedTask===t.id&&<div className="task-detail"><strong>验收标准</strong><p>{t.acceptance}</p></div>}
    <div className="task-card-bottom"><span><CheckSquare2 size={12}/>人工跟进</span><select aria-label={`任务状态：${t.title}`} value={t.status} disabled={busy} onChange={e=>void changeStatus(t,e.target.value as Task['status'])}>{Object.entries(states).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></div>
  </article>}
  function taskBoard(items:Task[],global=false){const list=items.filter(t=>(taskFilter==='all'||t.status===taskFilter)&&(t.title+t.description+projectName(t.project_id)).toLowerCase().includes(query.toLowerCase()));return <>
    <div className="toolbar"><div className="segmented">{[['all','全部'],...Object.entries(states)].map(([k,v])=><button className={taskFilter===k?'selected':''} key={k} onClick={()=>setTaskFilter(k)}>{v}</button>)}</div><div className="view-switch"><button className={taskView==='board'?'selected':''} aria-label="看板视图" onClick={()=>setTaskView('board')}><LayoutDashboard size={16}/></button><button className={taskView==='list'?'selected':''} aria-label="列表视图" onClick={()=>setTaskView('list')}><ListTodo size={17}/></button></div></div>
    {list.length===0?<Empty icon={<CheckCheck/>} title="这里暂时没有任务" description={items.length?'换一个筛选条件，看看其他任务。':'确认 PRD 并生成方案后，就可以拆解开发任务。'}/>:taskView==='list'?<div className="task-list">{list.map(t=>taskCard(t,global))}</div>:<div className="kanban">{Object.entries(states).filter(([key])=>taskFilter==='all'||taskFilter===key).map(([key,label])=><section className={`kanban-column ${key}`} key={key}><div className="column-title"><span className="state-dot"/><h3>{label}</h3><span className="count">{list.filter(t=>t.status===key).length}</span></div><div className="column-cards">{list.filter(t=>t.status===key).map(t=>taskCard(t,global))}{!list.some(t=>t.status===key)&&<div className="column-empty">暂无{label}的任务</div>}</div></section>)}</div>}
  </>}
  function docRows(docs:Doc[]){return <div className="document-table"><div className="table-head"><span>文档名称</span><span>所属产品</span><span>类型 / 版本</span><span>更新时间</span><span/></div>{docs.map(d=><button className="document-row" key={d.id} onClick={()=>setReader(d)}><span className="doc-title"><DocIcon kind={d.kind}/><span>{docTitle(d)}</span></span><span className="table-project">{projectName(d.project_id)}</span><span><Badge>{kinds[d.kind]}</Badge><small>V{d.version}</small></span><span className="muted">{date(d.created_at,true)}</span><ChevronRight size={16}/></button>)}</div>}
  const progress=currentJob&&<div className="job-banner"><span className="job-pulse"><LoaderCircle className="spin" size={19}/></span><div><strong>{jobKinds[currentJob.kind]}{currentJob.status==='queued'?'已排队':'正在进行'}</strong><p>{eventLines.at(-1)||'AI 正在整理内容，你可以继续浏览其他文档。'}</p></div><Badge tone="green">自动保存进度</Badge></div>
  function projectOverview(p:Project){
    const next=!currentPrd?{tab:'interview',title:'先把需求想清楚',copy:'从你的想法出发，整理一份可确认的 PRD。',action:'继续需求访谈'}:
      !plan||p.documents.plan?.stale?{tab:'plan',title:'下一步，准备技术方案',copy:`已确认 PRD V${currentPrd.version}，可以继续规划实现方式。`,action:'进入技术方案'}:
      !r.tasks.length||r.stale?{tab:'tasks',title:'把方案拆成任务',copy:'方案已准备好，继续明确具体任务和验收标准。',action:'进入任务拆解'}:
      {tab:'prd',title:'继续你的产品',copy:'需求、方案和任务都保存在左侧，可以随时继续。',action:'打开 PRD'}
    const documents=(['prd','plan','tasks','teardown'] as Kind[]).map(kind=>latest(r,kind)).filter((doc):doc is Doc=>!!doc)
    return <section className="simple-product-overview"><div className="simple-next-step"><h2>{next.title}</h2><p>{next.copy}</p><button className="button primary" onClick={()=>go('product',p.id,next.tab)}>{next.action}<ArrowRight size={15}/></button></div>
      {!!documents.length&&<div className="simple-document-list">{documents.map(doc=><button className="simple-document-row" key={doc.id} onClick={()=>go('product',p.id,doc.kind)}><DocIcon kind={doc.kind}/><span className="simple-document-title">{kinds[doc.kind]}</span><small>V{doc.version}</small><ChevronRight size={15}/></button>)}</div>}
    </section>
  }
  function documentPage(p:Project,kind:'prd'|'plan'){
    const doc=kind==='prd'?currentPrd:plan;const old=kind==='plan'&&p.documents.plan?.stale
    return <div className="document-layout"><section className="panel document-sheet"><div className="sheet-toolbar"><div><DocIcon kind={kind}/><strong>{kinds[kind]}</strong>{doc&&<Badge>V{doc.version}</Badge>}{old&&<Badge tone="amber">需要更新</Badge>}</div><div>{doc&&<button className="button compact" onClick={()=>download(doc.content,`${p.name}-${kinds[kind]}-V${doc.version}.md`)}><ArrowDownToLine size={15}/>下载</button>}{kind==='prd'&&<button className="button compact" disabled={projectBusy||busy} onClick={()=>openEdit(p.id,doc?.content||'',doc?.version||0,doc?'编辑需求 · 保存新版本':'手动编写 PRD')}><SquarePen size={15}/>{doc?'编辑新版':'手动编写'}</button>}{kind==='plan'&&doc&&<button className="button compact" disabled={projectBusy||busy||!currentPrd} onClick={()=>void generate(p,'plan')}><RefreshCw size={14}/>重新生成</button>}</div></div>
    {old&&<div className="inline-warning"><AlertCircle size={16}/>需求已有更新，这份方案基于旧版 PRD。重新生成后可继续拆解任务。</div>}
    {doc?<RenderDoc doc={doc}/>:<Empty icon={kind==='prd'?<FileText/>:<Workflow/>} title={kind==='prd'?'先准备一份产品需求':'让需求拥有清晰的实现路径'} description={kind==='prd'?'可以通过访谈生成草稿，也可以导入已有的 Markdown PRD。':'确认 PRD 后，AI 会为你推导架构、工具与实现建议。'} action={kind==='prd'?<div className="button-row"><button className="button primary" onClick={()=>go('product',p.id,'interview')}><Sparkles size={15}/>需求访谈</button><button className="button" disabled={projectBusy} onClick={()=>chooseImport(p)}><Import size={15}/>导入 PRD</button></div>:<button className="button primary" disabled={!currentPrd||projectBusy||busy} onClick={()=>void generate(p,'plan')}><Sparkles size={15}/>生成技术方案</button>}/>}</section>
    </div>

  }
  const assistantView=route.view==='assistant'||route.view==='overview'
  return <div className={`app-shell minimal-app${assistantView?' assistant-view':''}`}>
    {sidebar&&<div className="mobile-shade" onClick={()=>setSidebar(false)}/>}
    <aside className={`sidebar ${sidebar?'open':''}`}>
      <button className="brand" aria-label={productName} onClick={()=>go('assistant')}><span className="brand-symbol"><img src="/assets/brand/blue-path-mark.png" width="40" height="40" alt=""/></span><span className="brand-wordmark"><strong>Blue</strong><small>产品经理工作台</small></span></button>
      <div className="search-box sidebar-search"><Search size={15}/><input ref={searchRef} aria-label="搜索产品、文档或任务" placeholder="搜索…" value={query} onChange={e=>setQuery(e.target.value)}/><kbd>⌘ K</kbd></div>
      <nav className="main-nav" aria-label="工作空间导航">
        <button className={assistantView?'active':''} aria-current={assistantView?'page':undefined} onClick={()=>go('assistant')}><MessageSquare size={17}/><span>产品助理</span></button>
        <button className={route.view==='workbench'||route.view==='phase'?'active':''} aria-current={route.view==='workbench'||route.view==='phase'?'page':undefined} onClick={()=>go('workbench')}><LayoutDashboard size={17}/><span>工作台</span></button>
        <button className={route.view==='projects'||route.view==='product'?'active':''} aria-current={route.view==='projects'||route.view==='product'?'page':undefined} onClick={()=>go('projects')}><Folder size={17}/><span>全部产品</span></button>
        <button className={route.view==='documents'?'active':''} aria-current={route.view==='documents'?'page':undefined} onClick={()=>go('documents')}><BookOpen size={17}/><span>文档库</span></button>
        <button className={route.view==='tasks'?'active':''} aria-current={route.view==='tasks'?'page':undefined} onClick={()=>go('tasks')}><CheckSquare2 size={17}/><span>任务中心</span></button>
        <button className={route.view==='originals'?'active':''} aria-current={route.view==='originals'?'page':undefined} onClick={()=>go('originals')}><BookOpen size={17}/><span>原版工具</span></button>
      </nav>
      <section className="sidebar-projects" aria-label="我的产品"><div className="product-label"><h3 className="sidebar-section-label">我的产品</h3><button className="icon-button small" aria-label="新建产品" onClick={openCreate}><Plus size={16}/></button></div><div className="sidebar-products">{filteredProjects.map(p=><button className={project?.id===p.id?'selected':''} aria-current={project?.id===p.id?'page':undefined} key={p.id} onClick={()=>go('product',p.id,defaultProductTab(p))}><span className="project-dot"/><span>{p.name}</span></button>)}{!filteredProjects.length&&<p className="sidebar-empty">{query?'没有匹配的产品':'还没有产品'}</p>}</div></section>
      {project&&<div className="sidebar-current-section"><div className="sidebar-project-heading"><label htmlFor="current-product">当前产品</label><select id="current-product" className="sidebar-project-switcher" aria-label="选择产品" value={project.id} onChange={e=>{const selected=projects.find(p=>p.id===e.target.value);if(selected)go('product',selected.id,phase?phaseTab(phase,selected):defaultProductTab(selected))}}>{projects.map(p=><option value={p.id} key={p.id}>{p.name}</option>)}</select></div></div>}
      {project&&<><nav className="product-navigation" aria-label="当前产品导航">{productTabs.map(([tab,label])=><button className={route.tab===tab?'active':''} key={tab} aria-current={route.tab===tab?'page':undefined} onClick={()=>go('product',project.id,tab)}>{productTabIcons[tab]}<span>{label}</span>{tab==='interview'&&pending.some(p=>p.id===project.id)&&<i aria-label="有待确认草稿"/>}</button>)}</nav><a className="sidebar-export" href={`/api/projects/${project.id}/export`}><ArrowDownToLine size={15}/><span>导出资料</span></a></>}
      <div className="sidebar-bottom"><button className="settings-nav" onClick={()=>{setTrashTarget(null);setTrashOpen(true)}}><History size={17}/><span>回收站</span></button><button className={`connection ${model?.configured?'connected':''}`} onClick={()=>go('settings')}><span/>{model?.configured?'AI 已连接':'AI 待配置'}</button><button className={route.view==='settings'?'settings-nav active':'settings-nav'} onClick={()=>go('settings')}><Settings2 size={17}/><span>设置与技能</span></button></div>
    </aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="打开侧栏" onClick={()=>setSidebar(!sidebar)}><Menu size={19}/></button><strong>{project?.name||phase?.label||pageNames[route.view]||'工作台'}</strong>{project&&<details className="quiet-menu"><summary aria-label="产品操作">•••</summary><div><button onClick={()=>requestTrash({type:'project',item_id:project.id,title:project.name})}>移入回收站</button></div></details>}</div><div className="topbar-tools"><div className={`search-box ${searchExpanded?'expanded':''}`}><button className="search-toggle" type="button" aria-label="展开工作空间搜索" aria-expanded={searchExpanded} onClick={openSearch}><Search size={15}/></button><input ref={mobileSearchRef} placeholder="搜索产品、文档或任务" value={query} onChange={e=>setQuery(e.target.value)} aria-label="搜索工作空间"/>{searchExpanded?<button className="search-close" type="button" aria-label="收起搜索" onClick={()=>setSearchExpanded(false)}><X size={15}/></button>:<kbd>⌘ K</kbd>}</div></div></header>

    {(searchExpanded||query.trim())&&<FullSearch query={query} onQuery={setQuery} onClose={()=>{setQuery('');setSearchExpanded(false)}} onOpen={hit=>void openHit(hit)}/>}
    {trashOpen&&<TrashDialog onRequest={trashTarget} onClose={()=>{setTrashOpen(false);setTrashTarget(null)}} notify={notify} onRefresh={async()=>{await refresh();setToolsVersion(v=>v+1);if(trashTarget?.type==='project')go('projects')}}/>}
    <main className="main-content">
      {loadError&&<div className="error-banner"><AlertCircle size={17}/><span>暂时无法加载工作空间：{loadError}</span><button onClick={()=>void refresh()}>重新连接</button></div>}
      {loading?<div className="loading-workspace"><LoaderCircle className="spin"/><p>正在打开你的工作空间…</p></div>:<>
      {assistantView&&<Assistant key={toolsVersion} docs={allDocs} initialConversationId={route.id} initialMessageId={route.tab} onTrash={requestTrash} projects={projects} model={model} onRefresh={refresh} notify={notify} onOpenDocument={(pid,id)=>void openReferencedDocument(pid,id)} onOpenProduct={(pid,tab)=>go('product',pid,tab)} onSavedNote={()=>void refresh()}/>}
      {route.view==='workbench'&&<section className="phase-home"><header className="simple-page-heading"><h1>工作台</h1>{newButton}</header><nav className="home-phase-list" aria-label="工作台主入口">{phases.map((item,index)=><button className="home-phase-entry" key={item.key} aria-label={`${index+1}. ${item.label}`} onClick={()=>openPhase(item)}><span className="home-phase-index">0{index+1}</span><span className="home-phase-title">{item.label}</span><ChevronRight size={18}/></button>)}</nav></section>}
      {route.view==='projects'&&productList()}
      {route.view==='phase'&&productList(phase)}
      {route.view==='documents'&&<><div className="page-heading"><div><span className="eyebrow">KNOWLEDGE LIBRARY</span><h1>文档库</h1><p>所有需求、方案和历史版本，有序留在这里。</p></div><Badge>{allDocs.length} 份文档</Badge></div><div className="toolbar"><div className="segmented">{[['all','全部文档'],...Object.entries(kinds)].map(([key,label])=><button className={docFilter===key?'selected':''} key={key} onClick={()=>setDocFilter(key)}>{label}</button>)}</div><select className="filter-select" aria-label="按产品筛选文档" value={docProject} onChange={e=>setDocProject(e.target.value)}><option value="all">所有产品</option><option value="unassigned">未关联产品</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></div><section className="panel">{visibleDocs.length?docRows(visibleDocs):<Empty icon={<BookOpen/>} title="还没有符合条件的文档" description="试试切换文档类型或产品筛选。"/>}</section></>}
      {route.view==='tasks'&&<><div className="page-heading"><div><span className="eyebrow">MAKE PROGRESS</span><h1>任务中心</h1><p>跨产品查看待办，让每一次推进都有明确的落点。</p></div><span className="task-summary">已完成 <strong>{allTasks.filter(t=>t.status==='done').length}</strong> / {allTasks.length}</span></div>{taskBoard(allTasks,true)}</>}
      {route.view==='originals'&&<OriginalTools/>}
      {route.view==='settings'&&<><div className="page-heading"><div><span className="eyebrow">YOUR SPACE</span><h1>工作空间设置</h1><p>一个属于你的、安静而有序的产品空间。</p></div></div><div className="settings-grid"><section className="panel settings-panel"><div className="settings-icon"><Sparkles/></div><h2>AI 工作伙伴</h2><p>需求梳理、方案推导与任务拆解，按需运行。</p><div className="settings-row"><span>连接状态</span><Badge tone={model?.configured?'green':'amber'}>{model?.configured?'已连接':'待配置'}</Badge></div><div className="settings-row"><span>模型</span><strong>{model?.model||'DeepSeek'}</strong></div><div className="settings-row"><span>运行框架</span><strong>DeepSeek Harness</strong></div><div className="settings-row"><span>自动执行代码</span><span>尚未开放</span></div><button className="button" onClick={()=>void refresh()}><RefreshCw size={15}/>刷新连接状态</button></section><section className="panel settings-panel"><div className="settings-icon"><ShieldCheck/></div><h2>数据与工作方式</h2><p>重要的业务判断，始终由你确认。</p><div className="settings-row"><span>使用模式</span><strong>本机 · 单人</strong></div><div className="settings-row"><span>文档版本</span><span>保留历史，不覆盖</span></div><div className="settings-row"><span>AI 草稿</span><span>你确认后才成为 PRD</span></div><div className="settings-row"><span>生成中断</span><span>保留记录，手动重试</span></div><p className="settings-footnote">密钥只保存在本机配置文件，不在界面显示。模型推理会发送当前任务所需的上下文。</p></section></div></>}
      {route.view==='product'&&project&&<>
        {(phase?.key==='idea'||phase?.key==='backend')&&<header className="phase-heading"><div className="phase-heading-row"><h1>{phase.label}</h1><nav className="phase-switch" aria-label={`${phase.label}内容`}>{(phase.key==='idea'?[['interview','需求访谈'],['prd','PRD 文档']]:[['plan','技术方案'],['tasks','开发任务']]).map(([tab,label])=><button key={tab} aria-current={route.tab===tab?'page':undefined} className={route.tab===tab?'active':''} onClick={()=>go('product',project.id,tab)}>{label}</button>)}</nav></div><p>{phase.key==='idea'?'通过访谈理清想法，由你确认正式 PRD。':'整理技术方案与开发任务；代码执行尚未接入。'}</p></header>}
        {progress}{!projectBusy&&r.jobs[0]&&['failed','interrupted'].includes(r.jobs[0].status)&&<div className="inline-warning generation-warning"><AlertCircle size={17}/><div><strong>{jobKinds[r.jobs[0].kind]}{statusNames[r.jobs[0].status]}</strong><p>{r.jobs[0].error||'生成没有完成，已有文档会保留。'}</p></div><button className="button compact" onClick={()=>go('product',project.id,'history')}>查看记录与重试<ArrowRight size={13}/></button></div>}
        {route.tab==='overview'&&projectOverview(project)}
        {route.tab==='teardown'&&<Teardown key={project.id} project={project} resource={r} busy={busy||projectBusy} onRefresh={refresh} notify={notify}/>}
        {route.tab==='interview'&&<div className="interview-layout"><section className="panel chat-panel"><div className="panel-header"><h2>需求访谈</h2></div><div className="chat-messages">{!r.messages.length?<div className="chat-welcome"><h2>说说你的产品想法</h2></div>:r.messages.map(m=>{let content=m.content,questions:string[]=[];if(m.role==='assistant'){try{const parsed=JSON.parse(content);content=parsed.reply||content;questions=parsed.questions||[]}catch{/* 兼容纯文本 */}}return <div className={`chat-message ${m.role}`} key={m.seq}><span className={m.role==='assistant'?'ai-avatar':'avatar'}>{m.role==='assistant'?<Sparkles size={16}/>:'我'}</span><div className="message-body"><strong>{m.role==='assistant'?'需求伙伴':'我'}<time>{date(m.created_at,true)}</time></strong><p>{content}</p>{questions.length>0&&<ol>{questions.map(q=><li key={q}>{q}</li>)}</ol>}</div></div>})}{projectBusy&&<div className="thinking"><Sparkles size={15}/><span/>正在梳理你的想法…</div>}</div><form className="composer" onSubmit={send}><textarea aria-label="需求访谈输入" value={chat} onChange={e=>setChat(e.target.value)} maxLength={12000} placeholder="写下你的想法，或者回答上面的问题…" disabled={projectBusy} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter'){e.preventDefault();void send(e)}}}/><div><button className="button primary compact" type="submit" disabled={busy||projectBusy||!chat.trim()||!model?.configured}>{projectBusy?<LoaderCircle className="spin" size={15}/>:<Send size={14}/>}发送</button></div></form></section>{draft&&<details className="simple-draft"><summary>需求草稿 <small>V{draft.version}</small><ChevronDown size={15}/></summary><aside className="draft-rail"><section className="panel"><div className="panel-header"><h3>需求草稿</h3>{draft&&<Badge>V{draft.version}</Badge>}</div>{draft?<><div className="draft-excerpt"><RenderDoc doc={draft}/></div><button className="button full" onClick={()=>setReader(draft)}>完整阅读<ExternalLink size={14}/></button>{currentPrd?.metadata.draft_id===draft.id?<div className="confirmed-draft"><Badge tone="green"><Check size={11}/>已确认为 PRD V{currentPrd.version}</Badge><button className="text-button full" onClick={()=>go('product',project.id,'prd')}>查看正式 PRD<ArrowRight size={13}/></button></div>:draft.prd_id!==(currentPrd?.id||null)?<p className="aside-hint amber-text">这份草稿基于旧 PRD。请继续访谈，生成适用于当前版本的新草稿。</p>:<><button className="button primary full" disabled={busy||projectBusy} onClick={()=>void perform(()=>api(`/projects/${project.id}/prd/confirm`,'POST',{draft_id:draft.id,expected_version:currentPrd?.version||0,change_note:'在工作台确认需求草稿'}),'已确认为正式 PRD').then(ok=>ok&&go('product',project.id,'prd'))}><Check size={15}/>确认成为 PRD</button><button className="text-button full" disabled={projectBusy||busy} onClick={()=>openEdit(project.id,draft.content,currentPrd?.version||0,'编辑草稿 · 确认后保存为 PRD')}>编辑后再确认</button></>}</>:<div className="draft-placeholder"><FileText size={28}/><p>访谈后，AI 会把想法<br/>整理成可审阅的需求草稿。</p></div>}<p className="aside-hint"><ShieldCheck size={14}/>草稿经过你确认，才会成为正式需求。</p></section></aside></details>}</div>}
        {(route.tab==='prd'||route.tab==='plan')&&documentPage(project,route.tab)}
        {route.tab==='tasks'&&<>{r.stale&&<div className="inline-warning"><AlertCircle size={16}/>需求或方案已有更新。当前任务来自旧版本，可以查阅；请按最新方案重新拆解。</div>}<div className="task-section-heading"><div><h2>开发任务 <span className="title-count">{r.tasks.length}</span></h2><p>明确每一步要做什么，以及怎样才算完成。</p></div><button className="button primary" disabled={busy||projectBusy||!plan||!!project.documents.plan?.stale} onClick={()=>void generate(project,'tasks')}><Sparkles size={15}/>{r.tasks.length?'重新拆解任务':'拆解开发任务'}</button></div>{taskBoard(r.tasks)}</>}
        {(route.tab==='frontend'||route.tab==='deploy')&&<section className="phase-placeholder"><h1>{route.tab==='frontend'?'前端整合':'开发上线'}</h1><span className="phase-state">待接入</span><p>{route.tab==='frontend'?'根据同一份 PRD 和技术方案继续整合页面。当前尚未接入前端代码执行。':'承接前后端成果，完成构建与发布。当前尚未接入部署与线上验收。'}</p></section>}
        {route.tab==='history'&&<div className="history-layout"><section className="panel"><div className="panel-header"><h2>全部文档版本</h2><Badge>{r.docs.length} 份</Badge></div>{r.docs.length?docRows(r.docs):<Empty icon={<History/>} title="还没有版本记录" description="生成文档后，每个版本都会保留在这里。"/>}</section><section className="panel generation-records"><div className="panel-header"><h2>AI 生成记录</h2><span className="muted">包含失败与中断记录</span></div>{r.jobs.map(j=><div className="generation-row" key={j.id}><span className={`generation-status ${j.status}`}>{j.status==='done'?<CheckCircle2 size={18}/>:j.status==='running'?<LoaderCircle className="spin" size={18}/>:<AlertCircle size={18}/>}</span><div><strong>{jobKinds[j.kind]}</strong><small>{date(j.created_at,true)} · {statusNames[j.status]}{typeof j.usage.calls==='number'?` · ${j.usage.calls} 次模型响应`:''}{j.usage.token_usage_complete===false?' · 用量可能不完整':''}</small>{j.error&&<p>{j.error}</p>}</div>{['failed','interrupted'].includes(j.status)&&<button className="button compact" disabled={busy||projectBusy} onClick={()=>void perform(()=>api(`/jobs/${j.id}/retry`,'POST',{}),'已开始手动重试')}><RefreshCw size={13}/>重试</button>}</div>)}{!r.jobs.length&&<div className="calm-empty"><History size={20}/><p>还没有发起过 AI 生成。</p></div>}</section></div>}
      </>}
      {route.view==='product'&&!project&&<Empty title="没有找到这个产品" description="回到产品列表，打开一个已有产品。" action={<button className="button" onClick={()=>go('projects')}>回到产品列表</button>}/>}
      </>}
      {route.view==='settings'&&<InstalledSkills/>}
    </main></div>
    <input ref={importRef} type="file" accept=".md,text/markdown,text/plain" hidden onChange={e=>{void handleImport(e.target.files?.[0]);e.target.value=''}}/>
    {toast&&<div className={`toast ${toast.error?'error':''}`} role="status">{toast.error?<AlertCircle size={17}/>:<CheckCircle2 size={17}/>}<span>{toast.text}</span><button aria-label="关闭提示" onClick={()=>setToast(null)}><X size={14}/></button></div>}
    {creating&&<div className="modal-shade" onMouseDown={e=>{if(e.target===e.currentTarget)setCreating(false)}}><section className="modal create-modal" role="dialog" aria-modal="true" aria-labelledby="create-title"><button className="modal-close icon-button" aria-label="关闭新建产品" onClick={()=>setCreating(false)}><X size={19}/></button><h2 id="create-title">创建新产品</h2><p>先写下来，不必一开始就准备好完整的需求。</p><form onSubmit={create}><label>产品名称<input autoFocus required maxLength={80} placeholder="给它起个名字，例如：我的阅读助手" value={createName} onChange={e=>setCreateName(e.target.value)}/></label><label>一句话想法<textarea required maxLength={12000} placeholder="想为谁解决什么问题？目前的想法也可以。" value={createIdea} onChange={e=>setCreateIdea(e.target.value)}/></label><button className="button primary full" type="submit" disabled={busy||!createName.trim()||!createIdea.trim()}>{busy?<LoaderCircle className="spin" size={16}/>:<Plus size={16}/>}创建产品</button></form></section></div>}
    {reader&&<div className="modal-shade document-modal-shade"><section className="modal reader-modal" role="dialog" aria-modal="true" aria-label="阅读文档"><div className="reader-header"><DocIcon kind={reader.kind}/><div><strong>{kinds[reader.kind]} · V{reader.version}</strong><small>{projectName(reader.project_id)}</small></div><button className="button compact" onClick={()=>download(reader.content,`${kinds[reader.kind]}-V${reader.version}.md`)}><ArrowDownToLine size={14}/>下载文档</button><button className="icon-button" aria-label="关闭文档" onClick={()=>setReader(null)}><X size={20}/></button></div><button className="text-button" style={{padding:'10px 24px',textAlign:'right'}} onClick={()=>{const doc=reader;setReader(null);requestTrash({type:doc.kind==='note'?'note':'document',item_id:doc.id,title:docTitle(doc)})}}>移入回收站</button><div className="reader-body"><RenderDoc doc={reader} onReference={(pid,id)=>void openReferencedDocument(pid,id)}/></div></section></div>}
    {editor&&<div className="modal-shade document-modal-shade"><section className="modal editor-modal" role="dialog" aria-modal="true" aria-label={editor.title}><div className="reader-header"><SquarePen size={18}/><div><strong>{editor.title}</strong><small>基于 V{editor.version} · 保存为 V{editor.version+1}，保留历史</small></div><div className="segmented"><button className={!editingPreview?'selected':''} onClick={()=>setEditingPreview(false)}>编辑</button><button className={editingPreview?'selected':''} onClick={()=>setEditingPreview(true)}>预览</button></div><button className="icon-button" aria-label="关闭编辑器" onClick={()=>setEditor(null)}><X size={20}/></button></div>{editingPreview?<div className="editor-preview markdown"><Markdown remarkPlugins={[remarkGfm]}>{editor.content}</Markdown></div>:<textarea className="markdown-editor" aria-label="PRD 正文" maxLength={100000} placeholder="# 产品需求文档\n\n## 目标用户\n\n## 核心流程\n\n## 验收标准" value={editor.content} onChange={e=>setEditor({...editor,content:e.target.value})}/>}<div className="editor-footer"><input aria-label="本次更新说明" maxLength={300} value={editorNote} onChange={e=>setEditorNote(e.target.value)} placeholder="写一句本次更新说明"/><span>{editor.content.length} 字</span><button className="button primary" disabled={busy||!editor.content.trim()||!editorNote.trim()} onClick={()=>void perform(()=>api(`/projects/${editor.pid}/prd`,'POST',{content:editor.content,expected_version:editor.version,change_note:editorNote}),'新版本 PRD 已保存').then(ok=>{if(ok){go('product',editor.pid,'prd');setEditor(null)}})}><Check size={15}/>确认并保存新版</button></div></section></div>}
  </div>
}
