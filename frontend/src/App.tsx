import { FormEvent, ReactNode, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import {
  Activity, ArrowUpRight, BriefcaseBusiness, Building2, Check, ChevronRight,
  CircleAlert, Gauge, Inbox, LayoutDashboard, LoaderCircle, Mail, Menu, Radar,
  RefreshCw, Search, Send, Settings as SettingsIcon, SlidersHorizontal, Sparkles,
  UserRound, WandSparkles, X,
} from 'lucide-react'
import { api, ApiError, ApprovalItem, DiscoveryRun, Match, Profile } from './api'

const nav = [
  ['/', 'Overview', LayoutDashboard], ['/profile', 'Search profile', UserRound],
  ['/radar', 'Opportunity radar', Radar], ['/approval', 'Approval', Mail],
  ['/conversations', 'Conversations', Inbox], ['/studio', 'Draft studio', WandSparkles],
  ['/settings', 'Settings', SettingsIcon],
] as const

function App() {
  const [mobileOpen, setMobileOpen] = useState(false)
  return <div className="min-h-screen lg:grid lg:grid-cols-[250px_1fr]">
    <aside className={`fixed inset-y-0 left-0 z-40 w-[250px] border-r border-slate-800 bg-[#090f1b]/95 p-5 backdrop-blur-xl transition lg:sticky lg:top-0 lg:block lg:h-screen ${mobileOpen ? 'block' : 'hidden'}`}>
      <div className="mb-8 flex items-center justify-between">
        <NavLink to="/" className="flex items-center gap-3" onClick={() => setMobileOpen(false)}>
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-teal-300 to-indigo-400 text-lg font-black text-slate-950">S</span>
          <span><strong className="block tracking-tight text-white">Scoutly</strong><small className="text-slate-500">Private job copilot</small></span>
        </NavLink>
        <button className="lg:hidden" aria-label="Close navigation" onClick={() => setMobileOpen(false)}><X size={20}/></button>
      </div>
      <nav className="space-y-1">{nav.map(([to, label, Icon]) =>
        <NavLink key={to} to={to} end={to === '/'} onClick={() => setMobileOpen(false)} className={({isActive}) => `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${isActive ? 'bg-teal-300/10 text-teal-200' : 'text-slate-400 hover:bg-slate-800/60 hover:text-white'}`}>
          <Icon size={18}/>{label}
        </NavLink>
      )}</nav>
      <div className="absolute bottom-5 left-5 right-5 rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-teal-300"><span className="h-2 w-2 rounded-full bg-teal-300"/>Local workspace</div>
        <p className="m-0 text-xs leading-5 text-slate-500">Your job data and drafts stay in local SQLite.</p>
      </div>
    </aside>
    {mobileOpen && <button aria-label="Close menu overlay" className="fixed inset-0 z-30 bg-black/60 lg:hidden" onClick={() => setMobileOpen(false)}/>} 
    <div className="min-w-0">
      <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-800/80 bg-[#080d18]/85 px-4 backdrop-blur-xl lg:px-8">
        <button className="btn-secondary !p-2 lg:hidden" aria-label="Open navigation" onClick={() => setMobileOpen(true)}><Menu size={20}/></button>
        <PageCrumb/><div className="flex items-center gap-2 text-xs text-slate-500"><span className="hidden sm:inline">Safe send controls</span><span className="h-2 w-2 rounded-full bg-emerald-400"/></div>
      </header>
      <main className="mx-auto max-w-[1440px] px-4 py-7 sm:px-6 lg:px-10 lg:py-10">
        <Routes>
          <Route path="/" element={<Overview/>}/><Route path="/profile" element={<ProfilePage/>}/>
          <Route path="/radar" element={<RadarPage/>}/><Route path="/approval" element={<ApprovalPage/>}/>
          <Route path="/conversations" element={<Conversations/>}/><Route path="/studio" element={<Studio/>}/>
          <Route path="/settings" element={<SettingsPage/>}/><Route path="*" element={<Empty title="Page not found" text="That workspace view does not exist."/>}/>
        </Routes>
      </main>
    </div>
  </div>
}

function PageCrumb() {
  const { pathname } = useLocation()
  const current = nav.find(([path]) => path === pathname)?.[1] || 'Scoutly'
  return <div className="hidden items-center gap-2 text-sm sm:flex"><span className="text-slate-600">Workspace</span><ChevronRight size={14} className="text-slate-700"/><span className="font-medium text-slate-300">{current}</span></div>
}

function Heading({ eyebrow, title, text, action }: { eyebrow: string; title: string; text: string; action?: ReactNode }) {
  return <div className="mb-7 flex flex-col justify-between gap-5 md:flex-row md:items-end"><div><div className="eyebrow mb-2">{eyebrow}</div><h1 className="m-0 text-3xl font-semibold tracking-[-.04em] text-white sm:text-4xl">{title}</h1><p className="mb-0 mt-3 max-w-2xl leading-7 text-slate-400">{text}</p></div>{action}</div>
}

function QueryError({ error }: { error: Error }) { return <div className="panel border-rose-500/30 p-5 text-sm text-rose-300"><CircleAlert className="mr-2 inline" size={18}/>{error.message}</div> }
function Loading() { return <div className="panel grid min-h-48 place-items-center text-slate-400"><LoaderCircle className="animate-spin"/></div> }
function Empty({ title, text }: { title: string; text: string }) { return <div className="panel grid min-h-56 place-items-center border-dashed p-8 text-center"><div><Sparkles className="mx-auto mb-3 text-teal-300"/><h3 className="mb-2 text-lg text-white">{title}</h3><p className="m-0 text-sm text-slate-500">{text}</p></div></div> }

function Overview() {
  const client = useQueryClient()
  const stats = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard })
  const [runId, setRunId] = useState<string | null>(null)
  const run = useQuery({ queryKey: ['run', runId], queryFn: () => api.discoveryRun(runId!), enabled: !!runId, refetchInterval: runId ? 1200 : false })
  const start = useMutation({ mutationFn: api.startDiscovery, onSuccess: value => setRunId(value.id) })
  const finished = run.data && ['completed', 'partial', 'failed', 'interrupted'].includes(run.data.status)
  useEffect(() => { if (finished) { client.invalidateQueries(); } }, [finished, client])
  if (stats.isLoading) return <Loading/>
  if (stats.error) return <QueryError error={stats.error}/>
  const data = stats.data!
  return <>
    <section className="relative mb-6 overflow-hidden rounded-[28px] border border-indigo-400/20 bg-gradient-to-br from-slate-800 via-indigo-950/70 to-teal-950/70 p-7 shadow-2xl shadow-black/25 sm:p-10 lg:p-12">
      <div className="relative z-10 max-w-3xl"><div className="eyebrow mb-4">Your private job-search copilot</div><h1 className="m-0 text-4xl font-semibold leading-[1.04] tracking-[-.055em] text-white sm:text-6xl">Turn promising roles into real conversations.</h1><p className="mt-5 max-w-2xl text-base leading-7 text-slate-300 sm:text-lg">Discover relevant openings, understand the fit, and prepare grounded outreach—while you control every application and send.</p>
        <button className="btn-primary mt-7" disabled={start.isPending || (!!runId && !finished)} onClick={() => start.mutate()}>{start.isPending || (!!runId && !finished) ? <LoaderCircle size={17} className="animate-spin"/> : <Radar size={17}/>}Run discovery</button>
      </div><div className="absolute -right-24 -top-28 h-80 w-80 rounded-full border-[55px] border-teal-300/5"/>
    </section>
    {(run.data || start.error) && <RunProgress run={run.data} error={start.error}/>} 
    <div className="mb-7 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">{[
      {label:'Open roles',value:data.open_roles,Icon:BriefcaseBusiness}, {label:'Qualified',value:data.qualified,Icon:Gauge}, {label:'Applied',value:data.applied,Icon:Check},
      {label:'Awaiting approval',value:data.queued,Icon:Mail}, {label:'Sent',value:data.sent,Icon:Send}, {label:'Replies',value:data.replies,Icon:Inbox},
    ].map(({label, value, Icon}) => <div className="panel p-4" key={label}><Icon size={18} className="mb-4 text-teal-300"/><div className="text-2xl font-semibold text-white">{value}</div><div className="mt-1 text-xs text-slate-500">{label}</div></div>)}</div>
    <div className="grid gap-4 lg:grid-cols-3">{[
      ['01', 'Tune the search', 'Set titles, locations, preferences, and the companies you want to watch.', '/profile'],
      ['02', 'Review the evidence', 'Compare score, concrete fit evidence, gaps, and official apply links.', '/radar'],
      ['03', 'Approve with confidence', 'Apply first, refine your draft, check contact provenance, then send.', '/approval'],
    ].map(([number, title, text, href]) => <NavLink key={number} to={href} className="panel group p-5 transition hover:-translate-y-0.5 hover:border-teal-300/30"><div className="text-xs font-bold tracking-widest text-teal-300">{number}</div><h3 className="mb-2 mt-5 text-white">{title}</h3><p className="m-0 text-sm leading-6 text-slate-500">{text}</p><ArrowUpRight size={17} className="mt-5 text-slate-600 transition group-hover:text-teal-300"/></NavLink>)}</div>
  </>
}

function RunProgress({ run, error }: { run?: DiscoveryRun; error: Error | null }) {
  if (error) return <div className="panel mb-6 border-rose-500/30 p-4 text-sm text-rose-300">{error.message}</div>
  if (!run) return null
  return <div className="panel mb-6 p-5"><div className="mb-3 flex items-center justify-between"><div><span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-teal-300"/><span className="text-sm font-semibold capitalize text-white">{run.stage}</span></div><span className="text-xs text-slate-500">{run.progress}%</span></div><div className="h-1.5 overflow-hidden rounded-full bg-slate-800"><div className="h-full rounded-full bg-gradient-to-r from-teal-300 to-indigo-400 transition-all" style={{width: `${run.progress}%`}}/></div>{run.errors.map(item => <p key={item.stage} className="mb-0 mt-3 text-xs text-amber-300">{item.stage}: {item.message}</p>)}</div>
}

function ProfilePage() {
  const client = useQueryClient()
  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile })
  const sources = useQuery({ queryKey: ['sources'], queryFn: api.sources })
  const save = useMutation({ mutationFn: api.saveProfile, onSuccess: () => client.invalidateQueries({queryKey:['profile']}) })
  const add = useMutation({ mutationFn: api.addSource, onSuccess: () => client.invalidateQueries({queryKey:['sources']}) })
  if (profile.isLoading || sources.isLoading) return <Loading/>
  if (profile.error || sources.error) return <QueryError error={(profile.error || sources.error)!}/>
  return <><Heading eyebrow="Search controls" title="Your search profile" text="The matcher uses only these candidate facts and preferences. Add direct ATS boards for companies you care about."/>
    <div className="grid gap-6 xl:grid-cols-[1.25fr_.75fr]"><ProfileForm initial={profile.data} save={save}/><SourcesPanel sources={sources.data || []} add={add}/></div></>
}

function ProfileForm({ initial, save }: { initial: Profile | null | undefined; save: ReturnType<typeof useMutation<Profile, Error, Profile>> }) {
  const [form, setForm] = useState({ name: initial?.candidate_name || '', bio: initial?.candidate_profile || '', titles: list(initial?.preferences.desired_titles), locations: list(initial?.preferences.locations), types: list(initial?.preferences.employment_types), required: list(initial?.preferences.required_keywords), excluded: list(initial?.preferences.excluded_keywords), minimum: String(initial?.preferences.minimum_score || 70), remote: String(initial?.preferences.remote_policy || 'any') })
  const field = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setForm({...form, [key]: event.target.value})
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate({candidate_name:form.name,candidate_profile:form.bio,preferences:{desired_titles:csv(form.titles),locations:csv(form.locations),employment_types:csv(form.types),required_keywords:csv(form.required),excluded_keywords:csv(form.excluded),minimum_score:Number(form.minimum),remote_policy:form.remote}}) }
  return <form className="panel p-5 sm:p-7" onSubmit={submit}><div className="grid gap-5 sm:grid-cols-2"><label><span className="label">Name</span><input className="input" required value={form.name} onChange={field('name')}/></label><label><span className="label">Minimum fit score</span><input className="input" type="number" min="0" max="100" value={form.minimum} onChange={field('minimum')}/></label><label className="sm:col-span-2"><span className="label">Candidate facts</span><textarea className="input min-h-40 resize-y" required value={form.bio} onChange={field('bio')} placeholder="Skills, projects, education, and concrete achievements…"/></label><label><span className="label">Desired titles</span><input className="input" value={form.titles} onChange={field('titles')} placeholder="Backend Engineer, Python Developer"/></label><label><span className="label">Locations</span><input className="input" value={form.locations} onChange={field('locations')} placeholder="Bengaluru, Remote"/></label><label><span className="label">Employment types</span><input className="input" value={form.types} onChange={field('types')} placeholder="Full-time"/></label><label><span className="label">Remote policy</span><select className="input" value={form.remote} onChange={field('remote')}><option>any</option><option>remote only</option></select></label><label><span className="label">Required keywords</span><input className="input" value={form.required} onChange={field('required')}/></label><label><span className="label">Excluded keywords</span><input className="input" value={form.excluded} onChange={field('excluded')}/></label></div><div className="mt-6 flex items-center gap-3"><button className="btn-primary" disabled={save.isPending}>{save.isPending ? <LoaderCircle className="animate-spin" size={16}/> : <Check size={16}/>}Save profile</button>{save.isSuccess && <span className="text-sm text-emerald-300">Saved</span>}{save.error && <span className="text-sm text-rose-300">{save.error.message}</span>}</div></form>
}

function SourcesPanel({ sources, add }: { sources: Awaited<ReturnType<typeof api.sources>>; add: ReturnType<typeof useMutation<Awaited<ReturnType<typeof api.addSource>>, Error, {company_name:string;board_url:string}>> }) {
  const [company, setCompany] = useState(''), [url, setUrl] = useState('')
  const submit=(e:FormEvent)=>{e.preventDefault();add.mutate({company_name:company,board_url:url},{onSuccess:()=>{setCompany('');setUrl('')}})}
  return <div className="space-y-5"><form className="panel p-5" onSubmit={submit}><div className="mb-4 flex items-center gap-2 font-semibold text-white"><Building2 size={18} className="text-teal-300"/>Watch a company</div><label><span className="label">Company</span><input required className="input mb-4" value={company} onChange={e=>setCompany(e.target.value)}/></label><label><span className="label">Greenhouse or Lever board URL</span><input required type="url" className="input mb-4" value={url} onChange={e=>setUrl(e.target.value)}/></label><button className="btn-primary w-full" disabled={add.isPending}>Add source</button>{add.error&&<p className="text-sm text-rose-300">{add.error.message}</p>}</form><div className="panel divide-y divide-slate-800 overflow-hidden">{sources.length ? sources.map(source=><div key={source.id} className="flex items-center justify-between p-4"><div><div className="text-sm font-medium text-white">{source.company_name}</div><div className="mt-1 text-xs capitalize text-slate-500">{source.provider}</div></div><span className={`rounded-full px-2.5 py-1 text-xs ${source.enabled ? 'bg-emerald-400/10 text-emerald-300':'bg-slate-800 text-slate-500'}`}>{source.enabled?'Active':'Paused'}</span></div>):<div className="p-6 text-center text-sm text-slate-500">No company boards yet.</div>}</div></div>
}

function RadarPage() {
  const [score,setScore]=useState('0'), [company,setCompany]=useState('')
  const query=useMemo(()=>`?status=open&minimum_score=${score}${company?`&company=${encodeURIComponent(company)}`:''}`,[score,company])
  const matches=useQuery({queryKey:['matches',query],queryFn:()=>api.matches(query)})
  return <><Heading eyebrow="Ranked opportunities" title="Opportunity radar" text="See why each role matches, what is missing, and where to apply on the official posting." action={<div className="flex gap-2"><div className="relative"><Search size={15} className="absolute left-3 top-3 text-slate-600"/><input aria-label="Filter company" className="input !pl-9" value={company} onChange={e=>setCompany(e.target.value)} placeholder="Company"/></div><select aria-label="Minimum score" className="input w-28" value={score} onChange={e=>setScore(e.target.value)}><option value="0">All fits</option><option value="70">70+</option><option value="80">80+</option><option value="90">90+</option></select></div>}/>{matches.isLoading?<Loading/>:matches.error?<QueryError error={matches.error}/>:matches.data!.items.length?<div className="grid gap-4">{matches.data!.items.map(match=><MatchCard key={match.id} match={match}/>)}</div>:<Empty title="No roles on radar" text="Run discovery or loosen the current filters."/>}</>
}

function MatchCard({match}:{match:Match}) { return <article className="panel grid gap-5 p-5 md:grid-cols-[72px_1fr_auto] md:items-start"><div className="grid h-16 w-16 place-items-center rounded-2xl border border-teal-300/20 bg-teal-300/5 text-xl font-semibold text-teal-200">{match.score}</div><div><div className="flex flex-wrap items-center gap-2"><h2 className="m-0 text-lg font-semibold text-white">{match.title}</h2>{match.draft_id&&<span className="rounded-full bg-indigo-400/10 px-2 py-1 text-[11px] text-indigo-300">Draft prepared</span>}</div><p className="mb-4 mt-1 text-sm text-slate-500">{match.company_name} · {match.location||'Location not listed'}</p><div className="flex flex-wrap gap-2">{match.evidence.map(item=><span key={item} className="rounded-lg bg-emerald-400/8 px-2.5 py-1 text-xs text-emerald-300">✓ {item}</span>)}{match.missing.map(item=><span key={item} className="rounded-lg bg-amber-400/8 px-2.5 py-1 text-xs text-amber-300">Gap · {item}</span>)}</div></div><a className="btn-secondary" target="_blank" rel="noreferrer" href={match.apply_url||match.job_url}>Apply <ArrowUpRight size={15}/></a></article> }

function ApprovalPage() {
  const client=useQueryClient(), items=useQuery({queryKey:['approvals'],queryFn:api.approvals})
  const [selected,setSelected]=useState<number[]>([]), [confirm,setConfirm]=useState(false)
  const send=useMutation({mutationFn:api.send,onSuccess:()=>{setSelected([]);setConfirm(false);client.invalidateQueries()}})
  const toggle=(id:number)=>setSelected(current=>current.includes(id)?current.filter(value=>value!==id):[...current,id])
  return <><Heading eyebrow="Human approval" title="Application-first outreach" text="Every draft is editable. Sending stays locked until the role is open, you have applied, contact evidence exists, and you confirm the batch." action={<button className="btn-primary" disabled={!selected.length} onClick={()=>setConfirm(true)}><Send size={16}/>Send selected ({selected.length})</button>}/>{items.isLoading?<Loading/>:items.error?<QueryError error={items.error}/>:items.data!.length?<div className="space-y-5">{items.data!.map(item=><ApprovalCard key={item.id} item={item} selected={selected.includes(item.id)} toggle={()=>toggle(item.id)}/>)}</div>:<Empty title="No drafts prepared" text="A discovery run prepares drafts for the five strongest qualified roles."/>}{confirm&&<ConfirmModal count={selected.length} busy={send.isPending} error={send.error} close={()=>setConfirm(false)} confirm={()=>send.mutate(selected)}/>}</>
}

function ApprovalCard({item,selected,toggle}:{item:ApprovalItem;selected:boolean;toggle:()=>void}) {
  const client=useQueryClient(), [subject,setSubject]=useState(item.subject), [body,setBody]=useState(item.body)
  useEffect(()=>{setSubject(item.subject);setBody(item.body)},[item.subject,item.body])
  const invalidate=()=>client.invalidateQueries({queryKey:['approvals']})
  const save=useMutation({mutationFn:()=>api.saveDraft(item.id,{subject,body}),onSuccess:invalidate})
  const apply=useMutation({mutationFn:()=>api.markApplied(item.job_id),onSuccess:invalidate})
  const contact=useMutation({mutationFn:()=>api.findContact(item.job_id),onSuccess:invalidate})
  const personalize=useMutation({mutationFn:()=>api.regenerate(item.id),onSuccess:invalidate})
  const tone:Record<string,string>={'Draft ready':'bg-indigo-400/10 text-indigo-300','Contact pending':'bg-amber-400/10 text-amber-300','Ready after application':'bg-cyan-400/10 text-cyan-300','Ready to send':'bg-emerald-400/10 text-emerald-300','Sent':'bg-slate-700 text-slate-300'}
  return <article className={`panel overflow-hidden transition ${selected?'border-teal-300/40 ring-1 ring-teal-300/20':''}`}><div className="grid gap-5 border-b border-slate-800 p-5 lg:grid-cols-[1fr_auto]"><div><div className="mb-2 flex flex-wrap items-center gap-2"><span className={`rounded-full px-2.5 py-1 text-xs font-medium ${tone[item.display_state]}`}>{item.display_state}</span><span className="text-xs font-semibold text-teal-300">{item.score}% fit</span>{item.edited===1&&<span className="text-xs text-slate-500">Edited</span>}{item.stale===1&&<span className="text-xs text-amber-300">Contact update available</span>}</div><h2 className="m-0 text-xl font-semibold text-white">{item.title}</h2><p className="mb-0 mt-1 text-sm text-slate-500">{item.company_name} · {item.location||'Location not listed'}</p></div><div className="flex flex-wrap items-start gap-2"><a className="btn-secondary" target="_blank" rel="noreferrer" href={item.apply_url||item.job_url}>Open application <ArrowUpRight size={15}/></a>{item.application_status!=='applied'&&<button className="btn-primary" disabled={apply.isPending} onClick={()=>apply.mutate()}><Check size={15}/>Mark applied</button>}</div></div>
    <div className="grid gap-6 p-5 xl:grid-cols-[.68fr_1.32fr]"><div className="space-y-5"><section><div className="label">Recruiter evidence</div>{item.selected_contact_email?<div className="rounded-xl border border-slate-700 bg-slate-950/40 p-4"><div className="font-medium text-white">{item.contact_name||item.selected_contact_email}</div>{item.contact_name&&<div className="text-sm text-slate-400">{item.contact_position||'Recruiting contact'} · {item.selected_contact_email}</div>}<div className="mt-2 text-xs text-slate-500">{item.contact_source_kind} {item.contact_confidence?`· ${item.contact_confidence}% confidence`:''}</div>{item.contact_sources[0]&&<a href={item.contact_sources[0]} target="_blank" rel="noreferrer" className="mt-2 inline-block text-xs text-teal-300 hover:underline">View source evidence</a>}</div>:<div className="rounded-xl border border-dashed border-amber-400/20 bg-amber-400/5 p-4"><p className="m-0 text-sm text-amber-200">Draft is ready, but no verified recipient was found.</p><button className="btn-secondary mt-3 !py-2" disabled={contact.isPending} onClick={()=>contact.mutate()}><Search size={14}/>Find contact</button></div>}{item.selected_contact_email&&(item.stale===1||!item.contact_name)&&<button className="btn-secondary mt-3 w-full" disabled={personalize.isPending} onClick={()=>personalize.mutate()}><Sparkles size={15}/>Personalize with contact</button>}</section><section><div className="label">Send readiness</div><div className="space-y-2">{readiness(item).map(check=><div key={check.label} className={`flex items-start gap-2 text-xs ${check.ok?'text-slate-400':'text-amber-300'}`}>{check.ok?<Check size={14} className="shrink-0 text-emerald-300"/>:<CircleAlert size={14} className="shrink-0"/>}{check.label}</div>)}</div></section></div>
      <div><label><span className="label">Subject</span><input className="input" value={subject} disabled={item.display_state==='Sent'} onChange={e=>setSubject(e.target.value)}/></label><label className="mt-4 block"><span className="label">Message</span><textarea className="input min-h-64 resize-y leading-6" value={body} disabled={item.display_state==='Sent'} onChange={e=>setBody(e.target.value)}/></label><div className="mt-4 flex flex-wrap items-center justify-between gap-3"><button className="btn-secondary" disabled={save.isPending||item.display_state==='Sent'||(subject===item.subject&&body===item.body)} onClick={()=>save.mutate()}>{save.isPending?<LoaderCircle size={15} className="animate-spin"/>:<Check size={15}/>}Save changes</button><label className={`flex cursor-pointer items-center gap-2 text-sm ${item.can_send?'text-slate-200':'text-slate-600'}`}><input type="checkbox" className="h-4 w-4 accent-teal-300" checked={selected} disabled={!item.can_send} onChange={toggle}/>Include in batch</label></div>{(save.error||apply.error||contact.error||personalize.error)&&<p className="text-sm text-rose-300">{(save.error||apply.error||contact.error||personalize.error)?.message}</p>}</div></div></article>
}

function readiness(item:ApprovalItem){const blocked=new Set(item.blockers.map(value=>value.code));return [{label:'Role is still open',ok:!blocked.has('role_closed')},{label:'Application marked complete',ok:!blocked.has('application_required')},{label:'Verified contact and source evidence',ok:!blocked.has('contact_required')&&!blocked.has('contact_provenance_missing')},{label:'Subject and message are complete',ok:!blocked.has('draft_incomplete')},{label:'No previous send for this role',ok:!blocked.has('duplicate_send')},{label:'Daily Gmail capacity available',ok:!blocked.has('gmail_daily_limit')}]}

function ConfirmModal({count,busy,error,close,confirm}:{count:number;busy:boolean;error:Error|null;close:()=>void;confirm:()=>void}) { return <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4"><div role="dialog" aria-modal="true" aria-label="Confirm batch send" className="panel w-full max-w-md p-6"><div className="mb-4 grid h-11 w-11 place-items-center rounded-xl bg-teal-300/10 text-teal-300"><Send size={20}/></div><h2 className="text-xl text-white">Confirm {count} message{count===1?'':'s'}</h2><p className="leading-6 text-slate-400">This will send immediately through your connected Gmail account. The approved drafts become immutable delivery snapshots.</p>{error&&<p className="text-sm text-rose-300">{error.message}</p>}<div className="mt-6 flex justify-end gap-3"><button className="btn-secondary" onClick={close}>Cancel</button><button className="btn-primary" disabled={busy} onClick={confirm}>{busy?<LoaderCircle className="animate-spin" size={16}/>:<Send size={16}/>}Confirm and send</button></div></div></div> }

function Conversations(){const client=useQueryClient(), data=useQuery({queryKey:['conversations'],queryFn:api.conversations}), sync=useMutation({mutationFn:api.syncReplies,onSuccess:()=>client.invalidateQueries({queryKey:['conversations']})});return <><Heading eyebrow="Reply tracking" title="Conversations" text="Track sent outreach and human replies without losing the original message and Gmail thread IDs." action={<button className="btn-secondary" disabled={sync.isPending} onClick={()=>sync.mutate()}><RefreshCw size={16} className={sync.isPending?'animate-spin':''}/>Sync replies</button>}/>{data.isLoading?<Loading/>:data.error?<QueryError error={data.error}/>:data.data!.length?<div className="panel overflow-x-auto"><table className="w-full min-w-[700px] text-left text-sm"><thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-600"><tr><th className="p-4">Role</th><th>Recipient</th><th>Status</th><th>Sent</th><th>Thread</th></tr></thead><tbody>{data.data!.map((row:any)=><tr key={row.id} className="border-b border-slate-800/60"><td className="p-4"><div className="font-medium text-white">{row.title}</div><div className="text-xs text-slate-500">{row.company_name}</div></td><td className="text-slate-400">{row.recipient}</td><td><span className="rounded-full bg-teal-300/10 px-2 py-1 text-xs capitalize text-teal-300">{String(row.status).replace('_',' ')}</span></td><td className="text-slate-500">{row.sent_at?new Date(row.sent_at).toLocaleString():'—'}</td><td className="font-mono text-xs text-slate-600">{row.gmail_thread_id||'—'}</td></tr>)}</tbody></table></div>:<Empty title="No conversations yet" text="Approved outreach and replies will appear here."/>}</>}

function Studio(){
  const profile=useQuery({queryKey:['profile'],queryFn:api.profile})
  const [form,setForm]=useState({candidate_name:'',company_name:'',candidate_profile:'',job_description:'',recipient_name:'',recipient_position:'',role_title:'',applied_at:''})
  const [result,setResult]=useState(''), [uploadState,setUploadState]=useState('')
  useEffect(()=>{if(profile.data)setForm(current=>({...current,candidate_name:profile.data!.candidate_name,candidate_profile:profile.data!.candidate_profile}))},[profile.data])
  const generate=useMutation({mutationFn:()=>api.manualDraft(form),onSuccess:data=>setResult(data.body)})
  const change=(key:keyof typeof form)=>(e:React.ChangeEvent<HTMLInputElement|HTMLTextAreaElement>)=>setForm({...form,[key]:e.target.value})
  const upload=async(file:File|undefined,key:'candidate_profile'|'job_description')=>{if(!file)return;setUploadState(`Extracting ${file.name}…`);try{const data=await api.extractDocument(file);setForm(current=>({...current,[key]:data.text}));setUploadState(`${file.name} loaded`)}catch(error){setUploadState(error instanceof Error?error.message:'PDF extraction failed')}}
  return <><Heading eyebrow="One-off drafting" title="Draft studio" text="Generate a grounded email outside the discovery workflow. Paste text or extract a text-based PDF; nothing is sent or persisted automatically."/><div className="grid gap-6 xl:grid-cols-2"><form className="panel grid gap-4 p-6" onSubmit={e=>{e.preventDefault();generate.mutate()}}>{[['candidate_name','Candidate name'],['company_name','Company'],['role_title','Role title'],['recipient_name','Recipient name']].map(([key,label])=><label key={key}><span className="label">{label}</span><input required={key==='candidate_name'||key==='company_name'} className="input" value={form[key as keyof typeof form]} onChange={change(key as keyof typeof form)}/></label>)}<label><span className="label">Candidate facts</span><textarea required className="input min-h-32" value={form.candidate_profile} onChange={change('candidate_profile')}/><span className="mt-2 block text-xs text-slate-500">Or load PDF <input aria-label="Candidate profile PDF" type="file" accept="application/pdf,.pdf" className="ml-2 text-xs" onChange={e=>upload(e.target.files?.[0],'candidate_profile')}/></span></label><label><span className="label">Job description</span><textarea required className="input min-h-44" value={form.job_description} onChange={change('job_description')}/><span className="mt-2 block text-xs text-slate-500">Or load PDF <input aria-label="Job description PDF" type="file" accept="application/pdf,.pdf" className="ml-2 text-xs" onChange={e=>upload(e.target.files?.[0],'job_description')}/></span></label>{uploadState&&<p className="m-0 text-xs text-slate-400">{uploadState}</p>}<button className="btn-primary" disabled={generate.isPending}>{generate.isPending?<LoaderCircle className="animate-spin" size={16}/>:<WandSparkles size={16}/>}Generate draft</button>{generate.error&&<p className="text-sm text-rose-300">{generate.error.message}</p>}</form><div className="panel p-6"><div className="label">Generated message</div>{result?<textarea aria-label="Generated message" className="input min-h-[500px] leading-7" value={result} onChange={e=>setResult(e.target.value)}/>:<div className="grid min-h-[450px] place-items-center text-center text-sm text-slate-600"><div><WandSparkles className="mx-auto mb-3"/>Your generated message will appear here.</div></div>}</div></div></>
}

function SettingsPage(){const client=useQueryClient(),settings=useQuery({queryKey:['settings'],queryFn:api.settings}),connect=useMutation({mutationFn:api.connectGmail,onSuccess:()=>client.invalidateQueries({queryKey:['settings']})});return <><Heading eyebrow="Providers & safety" title="Settings" text="Check local OAuth, model, discovery-provider, and quota readiness."/>{settings.isLoading?<Loading/>:settings.error?<QueryError error={settings.error}/>:<div className="grid gap-5 lg:grid-cols-2"><section className="panel p-6"><h2 className="mt-0 text-lg text-white">Connections</h2><StatusRow label="Gmail" ok={settings.data!.gmail.connected} detail={settings.data!.gmail.connected?'Connected':'OAuth required'}/>{Object.entries(settings.data!.providers).map(([name,ok])=><StatusRow key={name} label={name} ok={ok} detail={ok?'Configured':'Not configured'}/>)}<button className="btn-primary mt-5" disabled={connect.isPending} onClick={()=>connect.mutate()}>{connect.isPending?<LoaderCircle className="animate-spin" size={16}/>:<Mail size={16}/>}Connect Gmail</button>{connect.error&&<p className="text-sm text-rose-300">{connect.error.message}</p>}</section><section className="panel p-6"><h2 className="mt-0 text-lg text-white">Quota guardrails</h2><div className="space-y-5">{Object.entries(settings.data!.quotas).map(([name,value])=><div key={name}><div className="mb-2 flex justify-between text-sm"><span className="capitalize text-slate-400">{name.replace('_',' ')}</span><span className="text-slate-500">{value.used} / {value.limit}</span></div><div className="h-2 rounded-full bg-slate-800"><div className="h-full rounded-full bg-teal-300" style={{width:`${Math.min(100,value.used/value.limit*100)}%`}}/></div></div>)}</div></section></div>}</>}
function StatusRow({label,ok,detail}:{label:string;ok:boolean;detail:string}){return <div className="flex items-center justify-between border-b border-slate-800 py-4"><span className="capitalize text-slate-300">{label}</span><span className={`flex items-center gap-2 text-xs ${ok?'text-emerald-300':'text-slate-500'}`}><span className={`h-2 w-2 rounded-full ${ok?'bg-emerald-300':'bg-slate-700'}`}/>{detail}</span></div>}

function list(value:unknown){return Array.isArray(value)?value.join(', '):typeof value==='string'?value:''}
function csv(value:string){return value.split(',').map(item=>item.trim()).filter(Boolean)}

export default App
