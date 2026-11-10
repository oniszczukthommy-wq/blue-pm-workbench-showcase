import {useCallback,useEffect,useRef,useState} from 'react'
import type {FormEvent} from 'react'
import {ArrowRight,BookOpen,Check,ChevronDown,FileText,LoaderCircle,Plus,RefreshCw,Send} from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {api} from './api'
import type {Model,Project} from './api'
import './assistant.css'
import {methods,ReferencePanel,ProposalReview} from './WorkspaceTools'
import type {Attachment,TrashTarget} from './WorkspaceTools'
import type {Doc} from './api'

export type Conversation={id:string;title:string;project_id:string|null;created_at:string;updated_at:string}
export type Reference={id:string;project_id:string;document_id:string;kind:string;title:string;version:number;created_at:string}
export type ChatAction={id:string;type:'teardown'|'draft'|'plan'|'tasks';label:string;payload:Record<string,unknown>;status:'proposed'|'executing'|'done'|'failed';result:{project_id:string;job_id:string;job_status?:string;document_id?:string}|null;error:string|null}
export type ChatMessage={id:string;conversation_id:string;role:'user'|'assistant';content:string;status:'queued'|'running'|'done'|'failed'|'interrupted';references:Reference[];actions:ChatAction[];usage:Record<string,unknown>;error:string|null;created_at:string;request_id?:string;saved_note_id?:string|null;skill_id?:string|null;attachments?:Attachment[];proposals?:{id:string;reason:string;status:string;result_id:string|null;project_id:string}[]}
export type Note={id:string;project_id:string|null;kind:'note';version:1;content:string;metadata:{conversation_id:string;message_id:string;references:Reference[];[key:string]:unknown};prd_id:null;created_at:string}
type Detail={conversation:Conversation;messages:ChatMessage[]}
export type AssistantProps={docs:Doc[];initialConversationId?:string;initialMessageId?:string;onTrash:(t:TrashTarget)=>void;projects:Project[];model:Model|null;onRefresh:()=>void|Promise<void>;notify:(message:string,error?:boolean)=>void;onOpenDocument:(projectId:string,documentId:string)=>void|Promise<void>;onOpenProduct:(projectId:string,tab:string)=>void;onSavedNote?:()=>void|Promise<void>}

const selectionKey='blue.workbench.conversation'
const activeStatuses=new Set(['queued','running'])
const actionTabs={teardown:'teardown',draft:'interview',plan:'plan',tasks:'tasks'}
const actionDescriptions={teardown:'联网收集公开资料，生成产品拆解。',draft:'把这轮讨论整理成需求草稿，正式 PRD 仍由你确认。',plan:'根据已确认的 PRD 整理技术方案。',tasks:'根据当前技术方案拆解开发任务。'}
function savedSelection(){try{return localStorage.getItem(selectionKey)||''}catch{return ''}}
function rememberSelection(id:string){try{if(id)localStorage.setItem(selectionKey,id);else localStorage.removeItem(selectionKey)}catch{/* 本机浏览器禁用存储时仍可聊天 */}}
function messageError(error:unknown){return error instanceof Error?error.message:'操作未完成，请稍后重试'}
function ordered(items:Conversation[]){return [...items].sort((a,b)=>b.updated_at.localeCompare(a.updated_at))}
function pending(detail:Detail|null){return !!detail?.messages.some(m=>activeStatuses.has(m.status)||m.actions?.some(a=>a.status==='executing'||activeStatuses.has(a.result?.job_status||'')))}
function jobLabel(action:ChatAction){
  const status=action.result?.job_status
  if(status==='done')return '已完成'
  if(status==='failed')return '生成未完成'
  if(status==='interrupted')return '生成已中断'
  if(status==='queued')return '已排队'
  if(status==='running')return '正在生成'
  if(action.status==='executing')return '正在发起'
  if(action.status==='done')return '已发起'
  return action.status==='failed'?'未能发起':''
}

export default function Assistant({docs,initialConversationId,initialMessageId,onTrash,projects,model,onRefresh,notify,onOpenDocument,onOpenProduct,onSavedNote}:AssistantProps){
  const [skillId,setSkillId]=useState('')
  const [attachments,setAttachments]=useState<Attachment[]>([])
  const [referenceOpen,setReferenceOpen]=useState(false)
  const [reviewId,setReviewId]=useState('')
  const [conversations,setConversations]=useState<Conversation[]>([])
  const [selectedId,setSelectedId]=useState('')
  const [newProjectId,setNewProjectId]=useState('')
  const [detail,setDetail]=useState<Detail|null>(null)
  const [drafts,setDrafts]=useState<Record<string,string>>({})
  const [initialLoading,setInitialLoading]=useState(true)
  const [detailLoading,setDetailLoading]=useState(false)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState<Set<string>>(new Set())
  const [savedNotes,setSavedNotes]=useState<Set<string>>(new Set())
  const [actionProjects,setActionProjects]=useState<Record<string,string>>({})
  const highlighted=useRef('')
  const mounted=useRef(false)
  const selected=useRef('')
  const epoch=useRef(0)
  const detailRequest=useRef(0)
  const busyRef=useRef(new Set<string>())
  const sendRequests=useRef<Record<string,{message:string;requestId:string}>>({})
  const retryRequests=useRef<Record<string,string>>({})
  const didChoose=useRef(false)
  const messagesRef=useRef<HTMLDivElement>(null)
  const inputRef=useRef<HTMLTextAreaElement>(null)
  const atBottom=useRef(true)
  const current=detail?.conversation.id===selectedId?detail:null
  const conversation=current?.conversation||conversations.find(c=>c.id===selectedId)
  const draftKey=selectedId||'new'
  const input=drafts[draftKey]||''
  const activeReply=!!current?.messages.some(m=>m.role==='assistant'&&activeStatuses.has(m.status))
  const sending=busy.has('send')
  const boundProjectId=conversation?.project_id||null
  const projectName=(id:string|null)=>id?projects.find(p=>p.id===id)?.name||'关联产品':'自由讨论'
  const take=(key:string)=>{if(busyRef.current.has(key))return false;busyRef.current.add(key);setBusy(new Set(busyRef.current));return true}
  const release=(key:string)=>{busyRef.current.delete(key);if(mounted.current)setBusy(new Set(busyRef.current))}

  const upsertConversation=useCallback((item:Conversation)=>{
    if(mounted.current)setConversations(previous=>{
      const existing=previous.find(c=>c.id===item.id)
      return existing&&existing.updated_at>item.updated_at?previous:ordered([item,...previous.filter(c=>c.id!==item.id)])
    })
  },[])
  const applyDetail=useCallback((data:Detail,id:string,viewEpoch:number,requestNumber:number)=>{
    if(!mounted.current)return
    upsertConversation(data.conversation)
    if(requestNumber>0&&selected.current===id&&epoch.current===viewEpoch&&detailRequest.current===requestNumber){setDetail(data);setError('')}
  },[upsertConversation])
  const readDetail=useCallback(async(id:string,viewEpoch=epoch.current)=>{
    const requestNumber=selected.current===id&&epoch.current===viewEpoch?++detailRequest.current:0
    const data=await api<Detail>(`/conversations/${id}`)
    applyDetail(data,id,viewEpoch,requestNumber)
    return data
  },[applyDetail])
  function choose(id:string){
    setAttachments([]);setSkillId('');setReferenceOpen(false)
    didChoose.current=true;selected.current=id;epoch.current++;detailRequest.current++
    setSelectedId(id);setDetail(null);setError('');setDetailLoading(!!id);rememberSelection(id);atBottom.current=true
  }
  function newConversation(){
    choose('');setNewProjectId('');setDrafts(previous=>({...previous,new:''}))
    requestAnimationFrame(()=>inputRef.current?.focus())
  }
  useEffect(()=>{
    mounted.current=true
    let cancelled=false
    void Promise.all([api<{items:Conversation[]}>('/conversations'),api<{items:Note[]}>('/notes').catch(()=>({items:[] as Note[]}))]).then(([list,notes])=>{
      if(cancelled)return
      const items=ordered(list.items);setConversations(items)
      setSavedNotes(new Set(notes.items.map(note=>note.metadata.message_id)))
      if(!didChoose.current){
        const remembered=initialConversationId||savedSelection(),id=items.find(c=>c.id===remembered)?.id||items[0]?.id||''
        selected.current=id;setSelectedId(id);setDetailLoading(!!id);rememberSelection(id)
      }
    }).catch(e=>{if(!cancelled)setError(messageError(e))}).finally(()=>{if(!cancelled)setInitialLoading(false)})
    return()=>{cancelled=true;mounted.current=false;epoch.current++;detailRequest.current++}
  },[])
  useEffect(()=>{
    if(!selectedId)return
    let cancelled=false
    const viewEpoch=epoch.current
    setDetailLoading(true)
    void readDetail(selectedId,viewEpoch).catch(e=>{if(!cancelled&&selected.current===selectedId&&epoch.current===viewEpoch)setError(messageError(e))}).finally(()=>{if(!cancelled&&selected.current===selectedId&&epoch.current===viewEpoch)setDetailLoading(false)})
    return()=>{cancelled=true}
  },[selectedId,readDetail])
  useEffect(()=>{if(initialConversationId&&initialConversationId!==selected.current)choose(initialConversationId)},[initialConversationId])
  useEffect(()=>{
    const key=`${initialConversationId}/${initialMessageId}`
    if(!initialMessageId||current?.conversation.id!==initialConversationId||!current?.messages.length||highlighted.current===key)return
    const element=document.getElementById('chat-message-'+initialMessageId)
    if(element){highlighted.current=key;atBottom.current=false;element.scrollIntoView({block:'center'});element.classList.add('message-search-target')}
  },[initialConversationId,initialMessageId,current?.conversation.id,current?.messages.length])
  const hasPending=pending(current)
  useEffect(()=>{
    if(!selectedId||!hasPending)return
    let cancelled=false,inFlight=false
    const id=selectedId,viewEpoch=epoch.current
    const poll=async()=>{
      if(cancelled||inFlight)return
      inFlight=true
      try{await readDetail(id,viewEpoch)}catch(e){if(!cancelled&&selected.current===id&&epoch.current===viewEpoch)setError(messageError(e))}finally{inFlight=false}
    }
    const timer=setInterval(()=>void poll(),1800)
    return()=>{cancelled=true;clearInterval(timer)}
  },[selectedId,hasPending,readDetail])
  useEffect(()=>{
    if(atBottom.current){const node=messagesRef.current;if(node)node.scrollTop=node.scrollHeight}
  },[current?.messages,detailLoading])
  async function refreshCurrent(){
    if(!selectedId)return
    const id=selectedId,viewEpoch=epoch.current
    try{await readDetail(id,viewEpoch)}catch(e){if(selected.current===id&&epoch.current===viewEpoch)setError(messageError(e))}
  }
  async function refreshRelated(){
    try{await onRefresh()}catch{/* 消息结果独立保存；主工作区自行处理刷新错误 */}
  }
  async function send(event?:FormEvent){
    event?.preventDefault()
    const message=input.trim()
    if(!message||activeReply||!model?.configured||!take('send'))return
    let id=selected.current
    const initialEpoch=epoch.current,key=id||'new',original=input
    let viewEpoch=initialEpoch
    const clearDraft=()=>{if(mounted.current)setDrafts(previous=>previous[key]===original?{...previous,[key]:''}:previous)}
    try{
      if(!id){
        const created=await api<Conversation>('/conversations','POST',{project_id:newProjectId||null,title:message.replace(/\s+/g,' ').slice(0,40)})
        id=created.id;upsertConversation(created)
        if(mounted.current&&selected.current===''&&epoch.current===initialEpoch){
          selected.current=id;setSelectedId(id);rememberSelection(id);setDetail({conversation:created,messages:[]})
          setDrafts(previous=>({...previous,[id]:original}));setDetailLoading(false)
        }
      }
      const request=sendRequests.current[id]
      const fingerprint=JSON.stringify({message,skillId,attachments})
      const requestId=request?.message===fingerprint?request.requestId:crypto.randomUUID()
      sendRequests.current[id]={message:fingerprint,requestId}
      detailRequest.current++
      const reply=await api<ChatMessage>(`/conversations/${id}/messages`,'POST',{message,request_id:requestId,skill_id:skillId||null,attachments:attachments.map(({document_id,quote})=>({document_id,quote:quote||null}))})
      delete sendRequests.current[id]
      if(mounted.current&&selected.current===id)setAttachments([])
      clearDraft()
      if(mounted.current)setDrafts(previous=>previous[id]===original?{...previous,[id]:''}:previous)
      if(mounted.current&&selected.current===id&&epoch.current===viewEpoch){
        setError('');atBottom.current=true
        setDetail(previous=>{
          if(previous?.conversation.id!==id||previous.messages.some(item=>item.id===reply.id))return previous
          const messages=previous.messages.some(item=>item.role==='user'&&item.request_id===requestId)?previous.messages:[...previous.messages,
            {id:`accepted-user-${requestId}`,conversation_id:id,role:'user' as const,content:message,status:'done' as const,references:[],actions:[],usage:{},error:null,created_at:reply.created_at,request_id:requestId}]
          return {...previous,messages:[...messages,reply]}
        })
      }
      try{await readDetail(id,viewEpoch)}catch(e){if(mounted.current&&selected.current===id&&epoch.current===viewEpoch)setError(messageError(e))}
    }catch(e){
      let accepted=false
      if(id){try{const data=await readDetail(id,viewEpoch);accepted=data.messages.some(m=>m.request_id===sendRequests.current[id]?.requestId)}catch{/* 原请求失败时保留输入和 request_id，供安全重试 */}}
      if(accepted){delete sendRequests.current[id];clearDraft();if(mounted.current)setDrafts(previous=>previous[id]===original?{...previous,[id]:''}:previous)}
      else if(mounted.current&&epoch.current===viewEpoch){setError(messageError(e));notify(messageError(e),true)}
    }finally{release('send')}
  }
  async function retry(message:ChatMessage){
    const key=`retry:${message.id}`,id=selected.current,viewEpoch=epoch.current
    if(!id||activeReply||!model?.configured||!take(key))return
    const requestId=retryRequests.current[message.id]||crypto.randomUUID();retryRequests.current[message.id]=requestId
    try{
      await api<ChatMessage>(`/conversations/${id}/messages/${message.id}/retry`,'POST',{request_id:requestId})
      delete retryRequests.current[message.id]
      await readDetail(id,viewEpoch)
    }catch(e){if(mounted.current&&selected.current===id&&epoch.current===viewEpoch){setError(messageError(e));void refreshCurrent()}}
    finally{release(key)}
  }
  async function saveNote(message:ChatMessage){
    const key=`note:${message.id}`,id=selected.current
    if(!id||message.saved_note_id||savedNotes.has(message.id)||!take(key))return
    try{
      await api<Note>(`/conversations/${id}/messages/${message.id}/notes`,'POST',{project_id:boundProjectId})
      if(mounted.current)setSavedNotes(previous=>new Set([...previous,message.id]))
      notify('讨论结论已保存到文档库')
      try{await onSavedNote?.()}catch{/* 结论已保存，文档列表可在进入时重新读取 */}
    }catch(e){if(mounted.current)notify(messageError(e),true)}finally{release(key)}
  }
  async function execute(message:ChatMessage,action:ChatAction){
    const key=`action:${action.id}`,id=selected.current,viewEpoch=epoch.current
    const target=boundProjectId||actionProjects[action.id]
    if(!id||!target||!model?.configured||!(action.status==='proposed'||(action.status==='failed'&&!action.result))||!take(key))return
    try{
      const result=await api<ChatAction>(`/conversations/${id}/messages/${message.id}/actions/${action.id}/execute`,'POST',{project_id:target})
      if(mounted.current&&selected.current===id&&epoch.current===viewEpoch){
        setDetail(previous=>previous?{...previous,messages:previous.messages.map(item=>item.id===message.id?{...item,actions:item.actions.map(a=>a.id===action.id?result:a)}:item)}:previous)
      }
      await refreshRelated()
      await readDetail(id,viewEpoch)
    }catch(e){if(mounted.current&&selected.current===id&&epoch.current===viewEpoch){setError(messageError(e));void refreshCurrent()}}
    finally{release(key)}
  }
  function actionCard(message:ChatMessage,action:ChatAction){
    const working=busy.has(`action:${action.id}`)||action.status==='executing'
    const result=action.result,label=jobLabel(action),jobFailed=['failed','interrupted'].includes(result?.job_status||'')
    return <div className="assistant-action" key={action.id}>
      <div className="assistant-action-copy"><strong>{action.label}</strong><p>{actionDescriptions[action.type]}</p>{action.type==='teardown'&&typeof action.payload.product_name==='string'&&<small>{action.payload.product_name}</small>}</div>
      {(action.status==='proposed'||(action.status==='failed'&&!result))&&<div className="assistant-action-controls">
        {!boundProjectId&&<select aria-label={`${action.label}的目标产品`} value={actionProjects[action.id]||''} onChange={e=>setActionProjects(previous=>({...previous,[action.id]:e.target.value}))} disabled={working}><option value="">选择目标产品</option>{projects.map(p=><option value={p.id} key={p.id}>{p.name}</option>)}</select>}
        <button className="button compact" onClick={()=>void execute(message,action)} disabled={working||!model?.configured||(!boundProjectId&&!actionProjects[action.id])}>{working?<LoaderCircle className="spin" size={14}/>:<ArrowRight size={14}/>} {action.status==='failed'?'重新发起':'确认发起'}</button>
      </div>}
      {label&&<div className={`assistant-action-result ${jobFailed||action.status==='failed'?'is-error':''}`}><span>{working||activeStatuses.has(result?.job_status||'')?<LoaderCircle className="spin" size={13}/>:result?.job_status==='done'?<Check size={13}/>:null}{label}</span>{result?.project_id&&<button onClick={()=>onOpenProduct(result.project_id,jobFailed?'history':actionTabs[action.type])}>{jobFailed?'查看记录与重试':result.job_status==='done'?'查看成果':'查看进度'}<ArrowRight size={12}/></button>}</div>}
      {action.error&&<p className="assistant-action-error" role="alert">{action.error}</p>}
    </div>
  }

  return <section className="pm-assistant" aria-label="产品助理聊天">
    <header className="assistant-header"><h1>产品助理</h1><div className="assistant-conversation-tools"><label className="assistant-conversation-picker"><span className="sr-only">切换对话</span><select aria-label="切换对话" value={selectedId} disabled={sending||initialLoading} onChange={e=>choose(e.target.value)}><option value="">新对话</option>{conversations.map(c=><option value={c.id} key={c.id}>{c.title}</option>)}</select><ChevronDown size={13}/></label>{selectedId&&<details className="quiet-menu"><summary aria-label="对话操作">•••</summary><div><button disabled={activeReply||sending} onClick={()=>onTrash({type:'conversation',item_id:selectedId,title:conversation?.title||'当前对话'})}>移入回收站</button></div></details>}<button className="assistant-new" onClick={newConversation} disabled={sending||initialLoading}><Plus size={15}/>新对话</button></div></header>
    <div className="assistant-context">
      {!selectedId?<label className="assistant-context-picker"><span>关联产品</span><select aria-label="新对话关联产品" value={newProjectId} disabled={sending} onChange={e=>{setNewProjectId(e.target.value);setAttachments([])}}><option value="">不关联，自由讨论</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>:<><span>{projectName(boundProjectId)}</span>{boundProjectId&&<button onClick={()=>onOpenProduct(boundProjectId,'overview')}>查看产品<ArrowRight size={12}/></button>}<small>{boundProjectId?'引用此产品的文档与任务':'未引用产品资料'}</small></>}
    </div>
    {error&&<div className="assistant-error" role="alert"><span>{error}</span>{selectedId&&<button onClick={()=>void refreshCurrent()}><RefreshCw size={13}/>重新加载</button>}</div>}
    <div className="assistant-messages" ref={messagesRef} onScroll={()=>{const node=messagesRef.current;if(node)atBottom.current=node.scrollHeight-node.scrollTop-node.clientHeight<100}} role="log" aria-label="对话记录" aria-live="polite" aria-relevant="additions text">
      {initialLoading||detailLoading?<div className="assistant-loading"><LoaderCircle className="spin" size={18}/><span>正在读取对话…</span></div>:!current?.messages.length?<div className="assistant-empty"><h2>今天想聊什么产品问题？</h2></div>:current.messages.map(message=><article id={`chat-message-${message.id}`} key={message.id} className={`assistant-message ${message.role}`}>
        <div className="assistant-message-name">{message.role==='user'?'你':'产品助理'}</div>
        {message.role==='user'&&message.skill_id&&<div className="assistant-method-tag">{methods.find(m=>m[0]===message.skill_id)?.[1]}</div>}
        {message.role==='user'&&message.attachments?.map((a,i)=><div className="attachment-chip" key={i}><BookOpen size={12}/><span>{a.quote?`引用段落：${a.quote}`:'已引用文档'}</span></div>)}
        {message.content&&<div className={message.role==='assistant'?'assistant-reply markdown':'assistant-user-text'}>{message.role==='assistant'?<Markdown remarkPlugins={[remarkGfm]} components={{img:({alt})=><span>{alt?`[图片：${alt}]`:'[图片]'}</span>}}>{message.content}</Markdown>:message.content}</div>}
        {activeStatuses.has(message.status)&&<div className="assistant-thinking"><LoaderCircle className="spin" size={15}/>{message.status==='queued'?'正在等待回复…':'正在思考…'}</div>}
        {message.role==='assistant'&&message.status==='done'&&<>
          {!!message.references?.length&&<div className="assistant-references" aria-label="本条回复引用资料">{message.references.map(reference=><button key={reference.id} onClick={()=>void Promise.resolve(onOpenDocument(reference.project_id,reference.document_id)).catch(e=>notify(messageError(e),true))}><BookOpen size={12}/><span>{reference.title}</span><small>V{reference.version}</small></button>)}</div>}
          {message.proposals?.map(proposal=><div className="assistant-proposal" key={proposal.id}><div><strong>{proposal.status==='saved'?'修改已保存':proposal.status==='rejected'?'修改已拒绝':'文档修改建议'}</strong><p>{proposal.reason}</p></div><button className="button compact" onClick={()=>setReviewId(proposal.id)}>查看差异</button></div>)}
          {!!message.actions?.length&&<div className="assistant-actions">{message.actions.map(action=>actionCard(message,action))}</div>}
          <div className="assistant-message-tools"><button onClick={()=>void saveNote(message)} disabled={!!message.saved_note_id||savedNotes.has(message.id)||busy.has(`note:${message.id}`)}>{busy.has(`note:${message.id}`)?<LoaderCircle className="spin" size={13}/>:message.saved_note_id||savedNotes.has(message.id)?<Check size={13}/>:<FileText size={13}/>} {message.saved_note_id||savedNotes.has(message.id)?'已保存到文档库':'保存讨论结论'}</button></div>
        </>}
        {message.role==='assistant'&&['failed','interrupted'].includes(message.status)&&<div className="assistant-failed" role="alert"><p>{message.error||(message.status==='interrupted'?'回复已中断，已有对话保留。':'这次回复未完成。')}</p><button className="text-button" onClick={()=>void retry(message)} disabled={activeReply||busy.has(`retry:${message.id}`)||!model?.configured}>{busy.has(`retry:${message.id}`)?<LoaderCircle className="spin" size={13}/>:<RefreshCw size={13}/>}重新回复</button></div>}
      </article>)}
    </div>
    <form className="assistant-composer" onSubmit={event=>void send(event)}>
      <div className="assistant-extra-tools"><select aria-label="选择产品方法" value={skillId} disabled={activeReply||sending} onChange={e=>setSkillId(e.target.value)}><option value="">自由讨论</option>{methods.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select><button type="button" disabled={!(boundProjectId||(!selectedId&&newProjectId))||activeReply||sending} onClick={()=>setReferenceOpen(true)}><BookOpen size={13}/>引用资料</button></div>
      {attachments.map(a=><div className="attachment-chip" key={a.document_id}><span>{a.title}{a.quote?` · ${a.quote.slice(0,45)}…`:''}</span><button type="button" aria-label={`移除引用 ${a.title}`} onClick={()=>setAttachments(old=>old.filter(x=>x.document_id!==a.document_id))}>×</button></div>)}
      <textarea ref={inputRef} aria-label="给产品助理发消息" placeholder={boundProjectId||newProjectId?'聊聊这个产品的用户、需求或下一步…':'写下想法、问题，或者让我们一起推敲一个产品…'} value={input} maxLength={12000} onChange={e=>setDrafts(previous=>({...previous,[draftKey]:e.target.value}))} disabled={sending||activeReply||initialLoading||detailLoading} onKeyDown={event=>{if(!event.nativeEvent.isComposing&&(event.metaKey||event.ctrlKey)&&event.key==='Enter'){event.preventDefault();void send()}}}/>
      <div className="assistant-composer-footer"><span>{!model?.configured?'请先在设置与技能中连接 AI':activeReply?'回复正在生成，切换对话后仍会保存':'⌘ / Ctrl + Enter 发送'}</span><button className="button primary compact" type="submit" disabled={!input.trim()||sending||activeReply||!model?.configured||initialLoading||detailLoading}>{sending||activeReply?<LoaderCircle className="spin" size={15}/>:<Send size={14}/>}发送</button></div>
    </form>
    {referenceOpen&&<ReferencePanel docs={docs.filter(d=>d.project_id===(boundProjectId||newProjectId))} onClose={()=>setReferenceOpen(false)} notify={notify} onAttach={a=>setAttachments(old=>old.length>=4&&!old.some(x=>x.document_id===a.document_id)?(notify('每条消息最多引用四份资料',true),old):[...old.filter(x=>x.document_id!==a.document_id),a])}/>}
    {reviewId&&<ProposalReview id={reviewId} onClose={()=>setReviewId('')} notify={notify} onChanged={async()=>{await refreshCurrent();await refreshRelated()}}/>}
  </section>
}
