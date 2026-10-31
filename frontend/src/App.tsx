import { useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { api, ApiError } from './api';
import type { Business, User } from './api';
import { Landing } from './Landing';
import { GoogleIntegrations } from './GoogleIntegrations';

const go = (path: string) => window.location.assign(path);
const message = (error: unknown) => error instanceof Error ? error.message : 'Please try again.';

function Shell({ user, children }: { user: User | null; children: ReactNode }) {
  const [error, setError] = useState('');
  async function logout() {
    try { await api('/auth/logout', {}); go('/login'); }
    catch (error) {
      if (error instanceof ApiError && error.status === 401) go('/login');
      else setError(message(error));
    }
  }
  return <div className="min-h-screen bg-[#f5f5ef] text-[#183f35]">
    <header className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-7">
      <a className="text-2xl font-bold" href="/">ReSlot AI</a>
      <nav aria-label="Main navigation" className="flex flex-wrap items-center gap-5 text-sm">
        {user ? <><a href="/businesses">Businesses</a><a href="/dashboard">Dashboard</a><a href="/settings/google">Settings</a>
          <button onClick={() => void logout()} className="underline">Log out</button></> :
          <><a href="/login">Log in</a><a href="/register">Register</a></>}
      </nav>
    </header>
    <main className="mx-auto max-w-5xl px-6 py-12">
      {error && <p role="alert" className="mb-6 text-red-800">{error}</p>}{children}
    </main>
  </div>;
}

function AuthForm({ registration }: { registration: boolean }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(''); setBusy(true);
    const data = new FormData(event.currentTarget);
    const credentials = { email: data.get('email'), password: data.get('password') };
    try {
      if (registration) await api('/auth/register', credentials);
      await api('/auth/login', credentials);
      go('/businesses');
    } catch (error) { setError(message(error)); setBusy(false); }
  }
  return <section className="panel mx-auto max-w-md">
    <p className="eyebrow">A fresh start</p>
    <h1 className="heading">{registration ? 'Create your account' : 'Welcome back'}</h1>
    <form onSubmit={event => void submit(event)} className="mt-7 grid gap-5">
      <label>Email<input className="field" name="email" type="email" autoComplete="email" required maxLength={254}/></label>
      <label>Password<input className="field" name="password" type="password" required
        minLength={registration ? 12 : 1} maxLength={128}
        autoComplete={registration ? 'new-password' : 'current-password'}/></label>
      {registration && <p className="text-sm text-[#5a6f65]">Use at least 12 characters. A long, unique passphrase works well.</p>}
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      <button className="primary" disabled={busy}>{busy ? 'Please wait?' : registration ? 'Create account' : 'Log in'}</button>
    </form>
    <p className="mt-6 text-sm">{registration ? 'Already registered? ' : 'New to ReSlot AI? '}
      <a className="underline" href={registration ? '/login' : '/register'}>{registration ? 'Log in' : 'Create an account'}</a></p>
  </section>;
}

function BusinessForm() {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(''); setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      await api('/tenants', Object.fromEntries(data.entries()));
      go('/dashboard');
    } catch (error) { setError(message(error)); setBusy(false); }
  }
  return <section className="panel mx-auto max-w-lg">
    <p className="eyebrow">Your next opening</p><h1 className="heading">Add a business</h1>
    <form onSubmit={event => void submit(event)} className="mt-7 grid gap-5">
      <label>Business name<input className="field" name="name" required maxLength={200} placeholder="Your business"/></label>
      <label>Business type<select className="field" name="business_type" defaultValue="other">
        <option value="restaurant">Restaurant</option><option value="hotel">Hotel</option><option value="consulting">Consulting office</option><option value="coworking">Coworking space</option><option value="beauty_salon">Beauty salon</option><option value="barbershop">Barbershop</option>
        <option value="wellness">Wellness studio</option><option value="other">Other appointment business</option>
      </select></label>
      <label>Timezone<input className="field" name="timezone" required maxLength={100}
        defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Paris'}/></label>
      <p className="text-sm text-[#5a6f65]">Use an IANA timezone, such as Europe/Paris.</p>
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      <button className="primary" disabled={busy}>{busy ? 'Creating?' : 'Create business'}</button>
    </form>
  </section>;
}

function BusinessSelection() {
  const [businesses, setBusinesses] = useState<Business[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { void api<Business[]>('/tenants').then(setBusinesses).catch(error => setError(message(error))); }, []);
  async function select(id: string) {
    setBusy(true); setError('');
    try { await api('/tenants/switch', { tenant_id: id }); go('/dashboard'); }
    catch (error) { setError(message(error)); setBusy(false); }
  }
  return <section><p className="eyebrow">Your workspaces</p><h1 className="heading">Choose a business</h1>
    <p className="mt-4 text-[#5a6f65]">Open a business you own or belong to.</p>
    {error && <p role="alert" className="mt-6 text-red-800">{error}</p>}
    {!businesses && !error && <p className="mt-6" role="status">Loading businesses?</p>}
    {businesses?.length === 0 && <p className="mt-8">No businesses yet. Create your first one to get started.</p>}
    <div className="my-8 grid gap-5 sm:grid-cols-2">{businesses?.map(business =>
      <article key={business.id} className="panel">
        <h2 className="text-xl font-semibold">{business.name}</h2>
        <p className="mt-3 text-sm">{business.role} ? {business.timezone} ? {business.status}</p>
        <button className="primary mt-6" disabled={busy || business.status !== 'active'}
          onClick={() => void select(business.id)}>Open business</button>
      </article>)}</div>
    <a className="inline-block underline" href="/businesses/new">+ Add a business</a>
  </section>;
}

function Dashboard({ user }: { user: User }) {
  const [business, setBusiness] = useState<Business | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    void api<Business>('/dashboard').then(setBusiness).catch(error => {
      if (error instanceof ApiError && error.status === 409) go('/businesses');
      else setError(message(error));
    });
  }, []);
  return <section><p className="eyebrow">Your business dashboard</p>
    <h1 className="heading">{business?.name ?? 'Welcome to ReSlot AI'}</h1>
    <p className="mt-4 text-[#5a6f65]">Signed in as {user.email}</p>
    {error && <p role="alert" className="mt-6 text-red-800">{error} <a className="underline" href="/businesses">Choose a business</a></p>}
    {!business && !error && <p className="mt-6" role="status">Loading your business?</p>}
    {business && <><dl className="panel mt-8 grid gap-5 sm:grid-cols-3">
      <div><dt className="eyebrow">Your role</dt><dd className="mt-2 capitalize">{business.role}</dd></div>
      <div><dt className="eyebrow">Timezone</dt><dd className="mt-2">{business.timezone}</dd></div>
      <div><dt className="eyebrow">Business status</dt><dd className="mt-2 capitalize">{business.status}</dd></div>
    </dl><div className="panel mt-6"><h2 className="text-xl font-medium">Ready for what comes next</h2>
      <p className="mt-3 text-[#5a6f65]">Connect Gmail, Calendar, and your customer spreadsheet to prepare your business for cancellation recovery automation.</p><a className="primary mt-5 inline-block" href="/settings/google">Configure integrations</a></div></>}
  </section>;
}

export function App() {
  const path = window.location.pathname;
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(path !== '/');
  const [error, setError] = useState('');
  useEffect(() => {
    if (path === '/') return;
    void api<User>('/auth/me').then(setUser).catch(error => {
      if (!(error instanceof ApiError && error.status === 401)) setError(message(error));
    }).finally(() => setLoading(false));
  }, [path]);
  if (path === '/') return <Landing />;
  if (loading) return <Shell user={null}><p role="status">Loading your workspace?</p></Shell>;
  if (error) return <Shell user={null}><p role="alert">{error}</p><button onClick={() => window.location.reload()} className="primary mt-5">Retry</button></Shell>;
  if (path === '/login' || path === '/register') {
    return <Shell user={user}>{user ? <><h1 className="heading">You're signed in</h1><a className="underline mt-6 inline-block" href="/businesses">Open your businesses</a></> : <AuthForm registration={path === '/register'}/>}</Shell>;
  }
  if (!user) return <Shell user={null}><AuthForm registration={false}/></Shell>;
  return <Shell user={user}>{path === '/businesses/new' ? <BusinessForm/> :
    path === '/settings/google' ? <GoogleIntegrations/> : path === '/businesses' ? <BusinessSelection/> : path === '/dashboard' ? <Dashboard user={user}/> :
    <><h1 className="heading">Page not found</h1><a className="underline" href="/businesses">Go to your businesses</a></>}</Shell>;
}
