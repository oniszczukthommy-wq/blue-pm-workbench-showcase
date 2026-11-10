import {useEffect,useState} from 'react'
import {BookOpen,ChevronDown,Check,FileText,LoaderCircle,X} from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {api} from './api'

type Skill={id:string;name:string;file_count:number;description:string;capability_note:string;installed_at:string}
type Detail={skill:Skill;content:string;references:{path:string;content:string}[]}

export default function InstalledSkills(){
  const [skills,setSkills]=useState<Skill[]>([])
  const [loading,setLoading]=useState(true)
  const [error,setError]=useState('')
  const [detail,setDetail]=useState<Detail|null>(null)
  useEffect(()=>{void api<{items:Skill[]}>('/skills').then(data=>setSkills(data.items)).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[])
  useEffect(()=>{const close=(e:KeyboardEvent)=>{if(e.key==='Escape')setDetail(null)};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close)},[])
  async function show(id:string){try{setDetail(await api<Detail>('/skills/'+id));setError('')}catch(e){setError((e as Error).message)}}
  function body(text:string){return text.replace(/^---\n[\s\S]*?\n---\n/,'')}
  function render(text:string){return <Markdown skipHtml remarkPlugins={[remarkGfm]} components={{a:({href,children})=>{
    const index=detail?.references.findIndex(reference=>reference.path===href||reference.path===`references/${href}`)??-1
    if(index>=0)return <button type="button" className="skill-reference-link" onClick={()=>{const element=document.getElementById(`skill-reference-${index}`) as HTMLDetailsElement|null;if(element){element.open=true;element.scrollIntoView({behavior:'smooth',block:'start'})}}}>{children}</button>
    if(href&&/^https?:\/\//i.test(href))return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    return <span>{children}</span>
  },img:({alt})=><span>{alt}</span>}}>{text}</Markdown>}
  return <section className="panel installed-skills"><div className="panel-header"><h2><BookOpen size={17}/> 已安装技能 <span className="title-count">{skills.length}</span></h2><span>项目技能库</span></div>
    {loading&&<p className="skills-empty"><LoaderCircle className="spin" size={14}/>正在读取技能库…</p>}
    {error&&<p className="skills-error" role="status">{error}</p>}
    {!loading&&!error&&!skills.length&&<p className="skills-empty">你安装的产品方法与技能会显示在这里。</p>}
    {skills.map(skill=><article className="installed-skill" key={skill.id}><span className="skill-icon"><BookOpen size={22}/></span><div className="installed-skill-info"><div><h3>{skill.name}</h3><span className="badge green"><Check size={10}/>已安装</span></div><p>{skill.description}</p><small>{skill.file_count} 份文档 · {skill.installed_at} 安装</small><p className="skill-capability-note">{skill.capability_note}</p></div><button className="button compact" onClick={()=>void show(skill.id)}>查看技能说明</button></article>)}
    {detail&&<div className="modal-shade document-modal-shade"><section className="modal reader-modal" role="dialog" aria-modal="true" aria-label="技能说明"><header className="reader-header"><BookOpen size={20}/><div><strong>{detail.skill.name}</strong><small>原始技能说明与参考文档</small></div><button className="icon-button" aria-label="关闭技能说明" onClick={()=>setDetail(null)}><X size={20}/></button></header><div className="reader-body"><div className="markdown">{render(body(detail.content))}<div className="skill-references"><h2>随附参考文档</h2>{detail.references.map((reference,index)=><details key={reference.path} id={`skill-reference-${index}`}><summary><FileText size={15}/>{reference.path}<ChevronDown size={14}/></summary>{render(reference.content)}</details>)}</div></div></div></section></div>}
  </section>
}
