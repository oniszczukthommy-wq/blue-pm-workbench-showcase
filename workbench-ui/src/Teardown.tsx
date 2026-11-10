import {useEffect,useMemo,useRef,useState} from 'react'
import {ArrowDownToLine,ArrowRight,BookOpen,Check,CheckCircle2,ChevronDown,Clock3,ExternalLink,Flag,History,Layers3,LoaderCircle,Search,ShieldCheck,TriangleAlert} from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {api,download} from './api'
import type {Doc,Job,Project,Resources} from './api'
import './teardown.css'

type Props={project:Project;resource:Resources;busy:boolean;onRefresh:()=>Promise<void>;notify:(text:string,error?:boolean)=>void}
type Source={id:string;title:string;url:string;channel:string;snippet:string;source_type:string}
type Channel={id:string;name:string;status:string;query:string;source_count:number;error:string}
type MatrixItem={label:string;value:string;tone:string}
type JourneyItem={stage:string;user:string;system:string;checkpoint:string;exception:string}
type ComparisonItem={label:string;product:string;alternatives:string}
type Insight={title:string;body:string;tone:string}
type Metadata={product_name:string;product_url:string;depth:string;review_note:string;sources:Source[];channels:Channel[];retrieval_usage:Record<string,unknown>;visuals:{matrix:MatrixItem[];journey:JourneyItem[];comparison:ComparisonItem[];insights:Insight[]}}
type VisualSection='matrix'|'journey'|'comparison'|'insights'
type MarkdownNode={type:string;depth?:number;value?:string;url?:string;children?:MarkdownNode[];data?:Record<string,unknown>}

const requiredChannels=[{id:'xiaohongshu',name:'小红书'},{id:'bilibili',name:'B站'},{id:'douyin',name:'抖音'},{id:'wechat',name:'微信公众号'},{id:'woshipm',name:'人人都是产品经理'},{id:'sspai',name:'少数派'},{id:'x',name:'X / 海外科技媒体'}]
const channelAliases:Record<string,string>={xiaohongshu:'xiaohongshu',xhs:'xiaohongshu',小红书:'xiaohongshu',bilibili:'bilibili',b站:'bilibili',douyin:'douyin',抖音:'douyin',wechat:'wechat',weixin:'wechat',微信公众号:'wechat',微信:'wechat',woshipm:'woshipm',人人都是产品经理:'woshipm',sspai:'sspai',少数派:'sspai',x:'x',twitter:'x',overseas:'x','x / 海外科技媒体':'x','x/海外科技媒体':'x'}
const terminalStatuses=['done','failed','interrupted']
function record(value:unknown):Record<string,unknown>{return value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{}}
function list(value:unknown):Record<string,unknown>[]{return Array.isArray(value)?value.map(record):[]}
function copy(value:unknown):string{return typeof value==='string'?value:typeof value==='number'?String(value):Array.isArray(value)?value.map(copy).filter(Boolean).join('；'):''}
function readMetadata(doc?:Doc):Metadata{return normalizeMetadata(doc?.metadata)}
function normalizeMetadata(value:unknown):Metadata{
  const m=record(value),nested=record(m.visuals),v=Object.keys(nested).length?nested:m
  return {product_name:copy(m.product_name),product_url:copy(m.product_url),depth:copy(m.depth),review_note:copy(m.review_note),retrieval_usage:record(m.retrieval_usage),
    sources:list(m.sources).map(s=>({id:copy(s.id),title:copy(s.title),url:copy(s.url),channel:copy(s.channel),snippet:copy(s.snippet),source_type:copy(s.source_type)})),
    channels:list(m.channels).map(c=>({id:copy(c.id),name:copy(c.name),status:copy(c.status),query:copy(c.query),source_count:typeof c.source_count==='number'?Math.max(0,c.source_count):0,error:copy(c.error)})),
    visuals:{matrix:list(v.matrix).map(i=>({label:copy(i.label),value:copy(i.value),tone:copy(i.tone)})),journey:list(v.journey).map(i=>({stage:copy(i.stage),user:copy(i.user),system:copy(i.system),checkpoint:copy(i.checkpoint),exception:copy(i.exception)})),comparison:list(v.comparison).map(i=>({label:copy(i.label),product:copy(i.product),alternatives:copy(i.alternatives)})),insights:list(v.insights).map(i=>({title:copy(i.title),body:copy(i.body),tone:copy(i.tone)}))}}
}
function safeUrl(value:string):string|undefined{try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:undefined}catch{return undefined}}
function tone(value:string,fallback='blue'){const aliases:Record<string,string>={red:'red',risk:'red',warning:'red',blue:'blue',primary:'blue',green:'green',success:'green',verified:'green',orange:'amber',amber:'amber',paid:'amber',purple:'purple',technology:'purple',ai:'purple',gray:'gray',grey:'gray',muted:'gray'};return aliases[value.toLowerCase()]||fallback}
function date(value:string){const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'时间未记录':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(parsed)}
function channelKey(value:string){return channelAliases[value.trim().toLowerCase()]||value.toLowerCase()}
const sourceLabels:Record<string,string>={search_content:'搜索返回正文',search_summary:'仅检索摘要',fetched_page:'已抓取正文'}
const usageLabels:Record<string,string>={requests:'联网请求',search_requests:'搜索请求',fetch_requests:'网页抓取',completed_requests:'完成请求',successful_nonempty_requests:'返回有效内容',failed_requests:'失败请求',interrupted_requests:'中断请求',cache_hits:'缓存复用'}
function EmptyVisual({children}:{children:string}){return <p className="td-visual-empty">{children}</p>}

function VisualReport({metadata,section}:{metadata:Metadata;section:VisualSection}){
  const {matrix,journey,comparison,insights}=metadata.visuals
  const text=(value:string)=><EvidenceText text={value} sources={metadata.sources}/>
  return <div className="td-visuals">
    {section==='matrix'&&<section className="td-visual-section" aria-labelledby="td-matrix-title"><div className="td-section-heading"><div><span className="td-section-index">01 / BUSINESS MAP</span><h3 id="td-matrix-title">它收钱，收的是哪件活？</h3></div><span className="td-small-label">业务矩阵</span></div>
      {matrix.length?<div className="td-matrix">{matrix.map((item,index)=><article className={`td-matrix-card td-tone-${tone(item.tone,index%3===1?'purple':index%3===2?'amber':'blue')}`} key={index}><div><span className="td-card-number">{String(index+1).padStart(2,'0')}</span><Layers3 size={16}/></div><h4>{text(item.label||'业务维度')}</h4><p>{text(item.value||'尚未确认')}</p></article>)}</div>:<EmptyVisual>此版本未保存业务矩阵，请在完整报告中查看业务分析。</EmptyVisual>}
    </section>}
    {section==='journey'&&<section className="td-visual-section" aria-labelledby="td-journey-title"><div className="td-section-heading"><div><span className="td-section-index">02 / USER JOURNEY</span><h3 id="td-journey-title">从一个意图，到一次交付</h3></div><span className="td-small-label">用户旅程</span></div>
      {journey.length?<><div className="td-journey-scroll" tabIndex={0} aria-label="横向用户旅程，可左右滚动"><div className="td-journey">{journey.map((step,index)=><div className="td-journey-unit" key={index}><article className={`td-journey-card td-tone-${['blue','purple','green','amber','gray'][index%5]}`}><span className="td-journey-number">{String(index+1).padStart(2,'0')}</span><h4>{step.stage||['入口','输入','过程','输出','交付'][index%5]}</h4><div className="td-journey-role">用户动作</div><p>{text(step.user||'尚未确认')}</p><div className="td-journey-system"><span>系统反馈</span><p>{text(step.system||'尚未确认')}</p></div></article>{index<journey.length-1&&<ArrowRight className="td-journey-arrow" size={17}/>}</div>)}</div></div><div className="td-journey-flags">{journey.filter(step=>step.checkpoint||step.exception).map((step,index)=><div className="td-journey-flag" key={index}><span><Flag size={11}/></span><div><strong>{step.stage}</strong>{step.checkpoint&&<p>确认点：{text(step.checkpoint)}</p>}{step.exception&&<p>异常路径：{text(step.exception)}</p>}</div></div>)}</div>{!journey.some(step=>step.checkpoint||step.exception)&&<p className="td-visual-note">此版本未记录确认点或异常路径，不能据此判断流程已获实测验证。</p>}</>:<EmptyVisual>此版本未保存旅程图，请在完整报告中查看用户路径。</EmptyVisual>}
    </section>}
    {section==='comparison'&&<section className="td-visual-section" aria-labelledby="td-comparison-title"><div className="td-section-heading"><div><span className="td-section-index">05 / THE ALTERNATIVES</span><h3 id="td-comparison-title">替掉了什么，优势在哪里</h3></div><span className="td-small-label">竞争对比</span></div>
      {comparison.length?<div className="td-comparison-scroll" tabIndex={0} aria-label="产品与替代方案对比"><table className="td-comparison"><thead><tr><th scope="col">比较维度</th><th scope="col"><span className="td-table-dot"/>{metadata.product_name||'当前产品'}</th><th scope="col">其他产品 / 原有做法</th></tr></thead><tbody>{comparison.map((row,index)=><tr key={index}><th scope="row">{text(row.label||'比较维度')}</th><td>{text(row.product||'尚未确认')}</td><td>{text(row.alternatives||'尚未确认')}</td></tr>)}</tbody></table></div>:<EmptyVisual>此版本未保存竞争对比，请在完整报告中查看市场分析。</EmptyVisual>}
    </section>}
    {section==='insights'&&!!insights.length&&<section className="td-visual-section td-insights-section" aria-labelledby="td-insights-title"><div className="td-section-heading"><div><span className="td-section-index">06 / PRODUCT INSIGHTS</span><h3 id="td-insights-title">值得带走的产品判断</h3></div></div><div className="td-insights">{insights.map((insight,index)=><article className={`td-insight td-tone-${tone(insight.tone,index===0?'red':index===1?'purple':'amber')}`} key={index}><span>{String(index+1).padStart(2,'0')}</span><h4>{text(insight.title||'产品洞察')}</h4><p>{text(insight.body||'尚未确认')}</p></article>)}</div></section>}
  </div>
}

function Evidence({metadata}:{metadata:Metadata}){
  const channels=requiredChannels.map(required=>metadata.channels.find(channel=>channelKey(channel.id)===required.id||channelKey(channel.name)===required.id)||{...required,status:'not_recorded',query:'',source_count:0,error:''})
  const extras=metadata.channels.filter(channel=>!requiredChannels.some(required=>channelKey(channel.id)===required.id||channelKey(channel.name)===required.id))
  const allChannels=[...channels,...extras]
  const covered=channels.filter(channel=>channel.status==='completed'&&channel.source_count>0).length
  const incomplete=channels.length-covered
  return <details className="td-evidence"><summary><span className="td-evidence-icon"><Search size={17}/></span><span><strong>调研证据与渠道覆盖</strong><small>{covered} / 7 必需渠道有结果 · {metadata.sources.length} 条公开网页证据{incomplete>0?` · ${incomplete} 路存在缺口`:''}</small></span><ChevronDown size={17}/></summary><div className="td-evidence-body"><p className="td-evidence-note"><ShieldCheck size={15}/>以下记录说明公开网页检索覆盖情况。检索结果与他人体验不等同于亲自使用或产品实测；结论应结合正文中的证据等级阅读。</p><RetrievalUsage usage={metadata.retrieval_usage}/><div className="td-channels">{allChannels.map((channel,index)=>{const ok=channel.status==='completed'&&channel.source_count>0;const failed=channel.status==='error';const noResults=channel.status==='no_results'||channel.status==='completed'&&!channel.source_count;return <article className={`td-channel ${ok?'is-complete':failed?'is-error':'is-gap'}`} key={`${channel.id}-${index}`}><div><strong>{channel.name||channel.id}</strong><span>{ok?<CheckCircle2 size={12}/>:failed?<TriangleAlert size={12}/>:<Clock3 size={12}/>} {ok?`${channel.source_count} 条结果`:failed?'检索失败':noResults?'未检索到结果':channel.status==='pending'?'尚未执行':'未记录覆盖'}</span></div>{channel.query&&<p>检索：{channel.query}</p>}{failed&&<p className="td-channel-error">{channel.error||'渠道检索未完成，请结合其他证据阅读。'}</p>}{noResults&&<p>本次未获得可用公开结果，不能视为没有相关用户反馈。</p>}</article>})}</div>{metadata.sources.length>0&&<div className="td-source-list"><h4>可追溯网页</h4>{metadata.sources.map((source,index)=>{const href=safeUrl(source.url);return <article className="td-source" key={`${source.id}-${index}`}><span className="td-source-number">{source.id||String(index+1)}</span><div><div className="td-source-title">{href?<a href={href} target="_blank" rel="noopener noreferrer">{source.title||source.url}<ExternalLink size={12}/></a>:<strong>{source.title||'来源链接不可用'}</strong>}<span>{source.channel}</span>{sourceLabels[source.source_type]&&<span className={`td-source-type td-source-type-${source.source_type}`}>{sourceLabels[source.source_type]}</span>}</div>{source.snippet&&<p>{source.snippet}</p>}{href&&<small>{new URL(href).hostname}</small>}</div></article>})}</div>}</div></details>
}

function RetrievalUsage({usage}:{usage:Record<string,unknown>}){
  const counts=Object.entries(usageLabels).filter(([key])=>typeof usage[key]==='number')
  const note=copy(usage.billing_note)
  if(!counts.length&&!note)return null
  return <div className="td-retrieval-usage"><strong>本次搜证用量</strong><div>{counts.map(([key,label])=><span key={key}>{label}<b>{String(usage[key])}</b></span>)}</div>{note&&<p>{note}</p>}</div>
}

export default function Teardown({project,resource,busy,onRefresh,notify}:Props){
  const docs=useMemo(()=>resource.docs.filter(doc=>(doc.kind as string)==='teardown').sort((a,b)=>b.version-a.version),[resource.docs])
  const [selectedId,setSelectedId]=useState<string|null>(null)
  const [productName,setProductName]=useState(()=>readMetadata(docs[0]).product_name||project.name)
  const [productUrl,setProductUrl]=useState(()=>readMetadata(docs[0]).product_url)
  const [focus,setFocus]=useState('')
  const [depth,setDepth]=useState<'standard'|'deep'>(()=>readMetadata(docs[0]).depth==='deep'?'deep':'standard')
  const [submitting,setSubmitting]=useState(false)
  const [pendingJob,setPendingJob]=useState<string|null>(null)
  const [refreshing,setRefreshing]=useState(false)
  const [researchLoading,setResearchLoading]=useState(false)
  const [failedResearch,setFailedResearch]=useState<{jobId:string;metadata:Metadata}|null>(null)
  const [validation,setValidation]=useState('')
  const submittingRef=useRef(false)
  const reportRef=useRef<HTMLElement>(null)
  const productInputRef=useRef<HTMLInputElement>(null)
  const selected=docs.find(doc=>doc.id===selectedId)||docs[0]
  const metadata=useMemo(()=>readMetadata(selected),[selected])
  const locked=busy||submitting||!!project.active_job||!!pendingJob
  const generating=submitting||!!pendingJob||project.active_job?.kind==='teardown'
  const latestJob=resource.jobs.filter(job=>job.kind==='teardown').sort((a,b)=>b.created_at.localeCompare(a.created_at))[0]
  const failedJob=latestJob&&['failed','interrupted'].includes(latestJob.status)?latestJob:undefined
  useEffect(()=>{const saved=readMetadata(docs[0]);setProductName(saved.product_name||project.name);setProductUrl(saved.product_url);setDepth(saved.depth==='deep'?'deep':'standard');setFocus('');setSelectedId(null);setPendingJob(null);setValidation('');setFailedResearch(null)},[project.id])
  useEffect(()=>{if(!pendingJob)return;const job=resource.jobs.find(item=>item.id===pendingJob);if(project.active_job?.id===pendingJob||job&&terminalStatuses.includes(job.status))setPendingJob(null)},[pendingJob,project.active_job,resource.jobs])
  async function refresh(){if(refreshing)return;setRefreshing(true);try{await onRefresh()}catch(error){notify(error instanceof Error?error.message:'刷新未完成，请稍后重试',true)}finally{setRefreshing(false)}}
  async function showFailedResearch(){
    if(!failedJob||researchLoading)return
    setResearchLoading(true)
    try{const result=await api<unknown>(`/jobs/${failedJob.id}/research`);setFailedResearch({jobId:failedJob.id,metadata:normalizeMetadata(result)})}
    catch(error){notify(error instanceof Error?error.message:'无法读取本次搜证记录',true)}
    finally{setResearchLoading(false)}
  }
  async function retryFailed(){
    if(locked||submittingRef.current||!failedJob)return
    submittingRef.current=true;setSubmitting(true);setValidation('')
    try{const job=await api<Job>(`/jobs/${failedJob.id}/retry`,'POST');setPendingJob(job.id);setSelectedId(null);setFailedResearch(null);await onRefresh();notify('已重试本次拆解，将优先复用已有搜证缓存。')}
    catch(error){notify(error instanceof Error?error.message:'本次拆解未能重试，请稍后再试',true)}
    finally{submittingRef.current=false;setSubmitting(false)}
  }
  async function start(){
    if(locked||submittingRef.current)return
    if(!productName.trim()){setValidation('请先填写要拆解的产品名称。');productInputRef.current?.focus();return}
    if(productName.trim().length>80){setValidation('产品名称最多填写 80 个字符。');productInputRef.current?.focus();return}
    if(focus.trim().length>2000){setValidation('关注的问题最多填写 2000 个字符。');return}
    if(productUrl.trim()&&!safeUrl(productUrl.trim())){setValidation('请填写完整的 http:// 或 https:// 产品网址。');return}
    setValidation('');submittingRef.current=true;setSubmitting(true)
    try{const job=await api<Job>(`/projects/${project.id}/teardown`,'POST',{product_name:productName.trim(),product_url:productUrl.trim(),focus:focus.trim(),depth});setPendingJob(job.id);setSelectedId(null);await onRefresh();notify('已开始联网拆解，报告完成后会自动出现在这里。')}
    catch(error){notify(error instanceof Error?error.message:'拆解未能开始，请稍后重试',true)}
    finally{submittingRef.current=false;setSubmitting(false)}
  }
  const reportName=metadata.product_name||project.name
  const brief=<div className="td-compact-brief">
    <div className="td-compact-main">
      <label className="td-compact-name" htmlFor={`td-name-${project.id}`}>
        产品名称
        <input ref={productInputRef} id={`td-name-${project.id}`} value={productName} onChange={event=>{setProductName(event.target.value);setValidation('')}} placeholder="例如：豆包、可灵、Notion AI" maxLength={80} disabled={locked}/>
      </label>
      <button type="button" className="button primary td-start-button" onClick={()=>void start()} disabled={locked}>{generating&&<LoaderCircle className="spin" size={15}/>} {generating?'正在拆解…':'开始联网拆解'}</button>
    </div>
    <div className="td-compact-meta">
      <details className="td-options">
        <summary>更多选项<ChevronDown size={14}/></summary>
        <div className="td-option-fields">
          <label htmlFor={`td-url-${project.id}`}>产品官网<span>选填</span><input id={`td-url-${project.id}`} type="url" value={productUrl} onChange={event=>{setProductUrl(event.target.value);setValidation('')}} placeholder="https://" maxLength={2000} disabled={locked}/></label>
          <label htmlFor={`td-focus-${project.id}`}>关注的问题<span>选填</span><textarea id={`td-focus-${project.id}`} value={focus} onChange={event=>setFocus(event.target.value)} placeholder="例如：用户为什么用？AI 和人怎样分工？" maxLength={2000} rows={2} disabled={locked}/></label>
          <fieldset className="td-compact-depth" disabled={locked}><legend>拆解深度</legend><label><input type="radio" name={`td-depth-${project.id}`} value="standard" checked={depth==='standard'} onChange={()=>setDepth('standard')}/>标准拆解</label><label><input type="radio" name={`td-depth-${project.id}`} value="deep" checked={depth==='deep'} onChange={()=>setDepth('deep')}/>六层深拆</label></fieldset>
        </div>
      </details>
      <p className="td-cost-note">联网与报告生成消耗 DeepSeek 用量。</p>
    </div>
    {validation&&<p className="td-validation" role="alert"><TriangleAlert size={13}/>{validation}</p>}
    {!!project.active_job&&project.active_job.kind!=='teardown'&&<p className="td-queue-note" role="status">当前任务完成后可开始拆解。</p>}
  </div>
  return <div className="teardown-workspace">
    {selected?<details className="td-new-teardown"><summary>新建拆解<ChevronDown size={14}/></summary>{brief}</details>:<section className="td-brief" aria-label="产品拆解输入">{brief}</section>}
    {generating&&<div className="td-generation" role="status" aria-live="polite"><LoaderCircle className="spin" size={16}/><strong>正在拆解…</strong><button type="button" className="button compact" disabled={refreshing} onClick={()=>void refresh()}>{refreshing&&<LoaderCircle className="spin" size={12}/>}刷新进度</button></div>}
    {!generating&&failedJob&&<div className="td-failure" role="status"><TriangleAlert size={18}/><div><strong>{failedJob.status==='interrupted'?'上次拆解已中断':'上次拆解未完成'}</strong><p>{failedJob.error||'请检查模型与网络连接后，点击「重试本次」继续。'}{selected?' 已有报告仍可阅读。':''}</p><small>重试使用本次原始输入，优先复用已有搜证缓存。</small></div><div className="td-failure-actions"><button type="button" className="button compact td-retry-button" disabled={locked} onClick={()=>void retryFailed()}><ArrowRight size={12}/>重试本次</button><button type="button" className="button compact" disabled={researchLoading} onClick={()=>void showFailedResearch()}>{researchLoading?<LoaderCircle className="spin" size={12}/>:<Search size={12}/>}查看本次搜证</button></div></div>}
    {!generating&&failedResearch?.jobId===failedJob?.id&&failedResearch&&<div className="td-failed-evidence"><Evidence metadata={failedResearch.metadata}/></div>}
    {selected?<section className="td-report" ref={reportRef} aria-labelledby="td-report-title"><header className="td-report-header"><div className="td-report-title"><span className="td-report-symbol"><BookOpen size={20}/></span><div><span className="td-eyebrow">TEARDOWN REPORT</span><h2 id="td-report-title">{reportName} · AI 研究草稿</h2><p><span>v{selected.version}</span><span>{date(selected.created_at)}</span><span>{metadata.depth==='deep'?'六层深拆':'标准拆解'}</span>{selected.id===docs[0]?.id&&<span className="td-latest">最新版本</span>}</p></div></div><div className="td-report-actions"><a className="button compact" href={`/api/projects/${encodeURIComponent(project.id)}/documents/${encodeURIComponent(selected.id)}/report.html`} download><ExternalLink size={14}/>下载彩色报告</a><button type="button" className="button compact" onClick={()=>download(selected.content,`${reportName.replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_')}-产品拆解-v${selected.version}.md`)}><ArrowDownToLine size={14}/>下载 Markdown</button></div></header>
      {metadata.review_note&&<p className="td-review-note"><ShieldCheck size={14}/><span>{metadata.review_note} 请结合来源审阅后使用。</span></p>}
      {docs.length>1&&<details className="td-history"><summary><History size={14}/><span>历史版本</span><small>{docs.length} 个版本</small><ChevronDown size={14}/></summary><div>{docs.map((doc,index)=>{const saved=readMetadata(doc);return <button type="button" className={selected.id===doc.id?'is-selected':''} key={doc.id} onClick={()=>setSelectedId(index===0?null:doc.id)}><span>v{doc.version}</span><strong>{saved.product_name||project.name}</strong><small>{date(doc.created_at)} · {saved.depth==='deep'?'六层深拆':'标准拆解'}</small>{selected.id===doc.id?<Check size={13}/>:index===0?<span className="td-history-latest">最新</span>:null}</button>})}</div></details>}
      <div className="td-report-body"><div className="td-reading-note"><span><i className="td-dot-fact"/>页面事实</span><span><i className="td-dot-inference"/>合理推断</span><span><i className="td-dot-unknown"/>尚未确认</span><p>推断不能代替事实。工作台以灰阶呈现，导出保留报告配色。</p></div><div className="td-full-report"><div className="td-full-report-title"><BookOpen size={15}/><span>完整拆解报告</span><small>0—6 章节</small></div><div className="markdown td-markdown"><ReportMarkdown content={selected.content} metadata={metadata}/></div></div><Evidence metadata={metadata}/></div>
    </section>:<p className="td-minimal-empty">输入产品名称，开始拆解。</p>}
  </div>
}

function evidenceParts(value:string,sources:Source[]):{text:string;source?:Source}[]{
  const known=new Map(sources.filter(source=>safeUrl(source.url)).map(source=>[source.id,source]))
  const parts:{text:string;source?:Source}[]=[]
  let end=0
  for(const match of value.matchAll(/\[(S[\da-fA-F]{12})\]/g)){
    const start=match.index??0
    if(start>end)parts.push({text:value.slice(end,start)})
    const source=known.get(match[1])
    parts.push({text:source?(source.title||source.id):match[0],source})
    end=start+match[0].length
  }
  if(end<value.length)parts.push({text:value.slice(end)})
  return parts
}

function EvidenceText({text,sources}:{text:string;sources:Source[]}){
  return <>{evidenceParts(text,sources).map((part,index)=>part.source?<a className="td-inline-source" key={index} href={safeUrl(part.source.url)} title={part.source.title} target="_blank" rel="noopener noreferrer">{part.text}<ExternalLink size={10}/></a>:<span key={index}>{part.text}</span>)}</>
}

function markdownText(node:MarkdownNode):string{return node.value??node.children?.map(markdownText).join('')??''}
function citationUrl(value:string):string|undefined{const safe=safeUrl(value);if(!safe)return undefined;const url=new URL(safe);url.hash='';return url.href}

function reportPlugin(sources:Source[]){
  return ()=> (tree:unknown)=>{
    const root=tree as MarkdownNode
    const marked=new Set<VisualSection>()
    let chapter=-1,journeyHeading:MarkdownNode|undefined,journeyFallback:MarkdownNode|undefined
    function mark(node:MarkdownNode,section:VisualSection){
      if(marked.has(section))return
      node.data={...node.data,hProperties:{...record(node.data?.hProperties),'data-td-visual':section}}
      marked.add(section)
    }
    for(const node of root.children??[]){
      if(node.type!=='heading')continue
      const title=markdownText(node).trim()
      if(node.depth===2){
        const match=title.match(/^([0-6０-６])(?:[.．、:：\s]|$)/)
        chapter=match?Number(match[1].normalize('NFKC')):-1
        if(chapter===1)mark(node,'matrix')
        if(chapter===2&&!journeyFallback)journeyFallback=node
        if(chapter===5)mark(node,'comparison')
        if(chapter===6)mark(node,'insights')
      }else if(node.depth===3&&chapter===2&&title.includes('用户旅程')&&!journeyHeading)journeyHeading=node
    }
    const journeyTarget=journeyHeading||journeyFallback
    if(journeyTarget)mark(journeyTarget,'journey')
    function references(parent:MarkdownNode){
      if(!parent.children||['link','linkReference','code','inlineCode','html','image','imageReference'].includes(parent.type))return
      parent.children=parent.children.flatMap(child=>{
        if(child.type!=='text'){references(child);return [child]}
        return evidenceParts(child.value||'',sources).map(part=>part.source?{type:'link',url:part.source.url,children:[{type:'text',value:part.text}]}:{type:'text',value:part.text})
      })
    }
    references(root)
  }
}

function ReportMarkdown({content,metadata}:{content:string;metadata:Metadata}){
  const plugin=useMemo(()=>reportPlugin(metadata.sources),[metadata.sources])
  const byUrl=new Map(metadata.sources.map(source=>[citationUrl(source.url),source]))
  const visual=(section:unknown)=>['matrix','journey','comparison','insights'].includes(String(section))?<VisualReport section={section as VisualSection} metadata={metadata}/>:null
  return <Markdown skipHtml remarkPlugins={[remarkGfm,plugin]} components={{
    h2:({children,node})=><><h2>{children}</h2>{visual(node?.properties['data-td-visual'])}</>,
    h3:({children,node})=><><h3>{children}</h3>{visual(node?.properties['data-td-visual'])}</>,
    a:({href,children})=>{const url=href?citationUrl(href):undefined;const source=url?byUrl.get(url):undefined;return source?<a href={safeUrl(source.url)} title={source.title} target="_blank" rel="noopener noreferrer">{source.title||children}<ExternalLink size={11}/></a>:<span>{children}</span>},
    img:({alt,src})=>{const url=typeof src==='string'?citationUrl(src):undefined;const source=url?byUrl.get(url):undefined;return source?<a href={safeUrl(source.url)} title={source.title} target="_blank" rel="noopener noreferrer">{source.title||alt||'查看来源图片'}<ExternalLink size={11}/></a>:<span>{alt||'图片'}</span>}
  }}>{content}</Markdown>
}
