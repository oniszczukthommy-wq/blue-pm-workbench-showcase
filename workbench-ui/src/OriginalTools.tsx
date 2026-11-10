import {useState} from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {api} from './api'

export default function OriginalTools(){
  const [url,setUrl]=useState('')
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [guide,setGuide]=useState('')
  const [path,setPath]=useState('')
  const [copied,setCopied]=useState(false)
  async function start(){
    setError('');setBusy(true)
    try{const result=await api<{url:string}>('/integrations/agent/start','POST',{});setUrl(result.url)}
    catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function readGuide(){
    setError('')
    try{const result=await api<{content:string;path:string}>('/integrations/starter/guide');setGuide(result.content);setPath(result.path)}
    catch(e){setError((e as Error).message)}
  }
  async function openFolder(){
    setError('')
    try{await api('/integrations/starter/open-folder','POST',{})}catch(e){setError((e as Error).message)}
  }
  async function copyPrompt(){
    setError('')
    try{await navigator.clipboard.writeText('请读取项目根目录的 AGENTS.md，按其中“启动流程”开始。');setCopied(true)}catch{setError('复制失败，请手动复制下方启动句。')}
  }
  return <section className="original-tools">
    <header className="simple-page-heading"><h1>原版工具</h1><p>保留上游原项目，按各自的方式使用。</p></header>
    {error&&<p className="original-error" role="alert">{error}</p>}
    <div className="original-grid">
      <article className="panel original-card"><h2>Agent 工坊</h2><p>打开原版网页，使用产品循环、试玩、项目检查和交接包等上游功能。</p><p>工坊有独立的产品资料和模型设置；不会自动同步到 Blue 的产品列表。</p><div className="original-actions"><button className="button primary" disabled={busy} onClick={()=>void start()}>{busy?'正在启动…':'启动原版工坊'}</button>{url&&<a className="button" href={url} target="_blank" rel="noreferrer">打开原版工坊 ↗</a>}</div><small>Windows 下上游的 macOS 文件夹选择器不可用，可手动输入路径。</small></article>
      <article className="panel original-card"><h2>AI 产品开发工具包</h2><p>完整保留原版规则卡、手册、模板及阶段检查脚本。请在 Codex 等编码工具中打开此文件夹，再发送下面的启动句。</p><p>请读取项目根目录的 AGENTS.md，按其中“启动流程”开始。</p><p>上游仅声明支持 macOS；Windows 上的开发、验证和部署流程尚未全部验证。</p><div className="original-actions"><button className="button primary" onClick={()=>void openFolder()}>打开工具包文件夹</button><button className="button" onClick={()=>void copyPrompt()}>{copied?'已复制启动句':'复制启动句'}</button><button className="button" onClick={()=>void readGuide()}>{guide?'重新读取说明':'查看原版说明'}</button></div>{path&&<small>{path}</small>}</article>
    </div>
    {url&&<section className="panel original-embedded"><h2>Agent 工坊原版界面</h2><iframe title="Agent 工坊原版界面" src={url}/></section>}
    {guide&&<article className="panel original-guide"><div className="original-guide-head"><h2>工具包原版说明</h2><button className="button" onClick={()=>setGuide('')}>收起</button></div><div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{guide}</Markdown></div></article>}
  </section>
}
