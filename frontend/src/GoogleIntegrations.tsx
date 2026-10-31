import { useEffect, useState } from 'react';
import { api, ApiError } from './api';
import type { Business } from './api';

type SheetConfig = { configured: boolean; spreadsheet_id: string | null; requires_reconsent: boolean };
type Connection = { configured: boolean; connection_status: string; email: string | null };
type Calendar = { id: string; calendar_name: string; timezone: string; access_role: string; is_selected: boolean };
type Mail = { email: string; messages: { id: string; sender: string; subject: string; date: string }[] };
type Events = { calendar_name: string; timezone: string; events: { id: string; summary: string; start: string; end: string }[] };
const explain = (error: unknown) => error instanceof Error ? error.message : 'Please retry.';
const outcomes: Record<string, string> = {
  connected: 'Google is connected. You can preview your inbox and select a calendar.',
  access_denied: 'Google access was declined. You can connect again when ready.',
  invalid_state: 'This connection attempt is invalid or already used. Please start again.',
  expired_state: 'The connection attempt expired. Please start again.',
  invalid_callback: 'Google returned an invalid callback. Please start again.',
  missing_refresh_token: 'Offline access was not granted. Reconnect and approve the consent request.',
  missing_scopes: 'All requested permissions are needed. Reconnect and grant each permission.',
  invalid_google_identity: 'The Google identity could not be verified. Please try again.',
  account_mismatch: 'Disconnect the existing account before choosing a different Google account.',
  google_not_configured: 'Google integration needs to be configured on the server.',
  disconnect_pending: 'Retry Disconnect before reconnecting Google.',
};

export function GoogleIntegrations() {
  const [sheet, setSheet] = useState<SheetConfig | null>(null);
  const [spreadsheetId, setSpreadsheetId] = useState('');
  const [sheetMessage, setSheetMessage] = useState('');
  const [business, setBusiness] = useState<Business | null>(null);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [calendarsLoaded, setCalendarsLoaded] = useState(false);
  const [mail, setMail] = useState<Mail | null>(null);
  const [events, setEvents] = useState<Events | null>(null);
  const [error, setError] = useState('');
  const [mailError, setMailError] = useState('');
  const [calendarError, setCalendarError] = useState('');
  const [eventError, setEventError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice] = useState(() => {
    const outcome = new URLSearchParams(window.location.search).get('oauth');
    return outcome ? outcomes[outcome] ?? 'Google authorization could not be completed. Try connecting again.' : '';
  });
  useEffect(() => {
    // Remove the outcome marker from browser history after reading it.
    if (window.location.search) window.history.replaceState(null, '', '/settings/google');
    void api<Business>('/dashboard').then(setBusiness).catch(error => setError(explain(error)));
    void api<Connection>('/integrations/google').then(setConnection).catch(error => setError(explain(error)));
    void api<SheetConfig>('/integrations/sheets').then(value => { setSheet(value); setSpreadsheetId(value.spreadsheet_id ?? ''); }).catch(error => setError(explain(error)));
  }, []);
  async function refreshStatus() {
    const status = await api<Connection>('/integrations/google');
    setConnection(status);
    return status;
  }
  async function connect() {
    setError(''); setBusy(true);
    try {
      const result = await api<{ authorization_url: string }>('/integrations/google/start', {});
      const url = new URL(result.authorization_url);
      if (url.origin !== 'https://accounts.google.com') throw new Error('Unexpected Google authorization URL.');
      window.location.assign(url.href);
    } catch (error) { setError(explain(error)); setBusy(false); }
  }
  async function disconnect() {
    setError(''); setBusy(true);
    try {
      await api('/integrations/google/disconnect', {});
      setMail(null); setEvents(null); setCalendars([]);
      setMailError(''); setCalendarError(''); setEventError('');
    } catch (error) { setError(explain(error)); }
    finally {
      await refreshStatus().catch(error => setError(explain(error)));
      setBusy(false);
    }
  }
  async function loadMail() {
    setBusy(true); setMailError(''); setMail(null);
    try { setMail(await api<Mail>('/integrations/google/gmail')); }
    catch (error) { setMailError(explain(error)); await refreshStatus().catch(() => undefined); }
    finally { setBusy(false); }
  }
  async function loadCalendars() {
    setBusy(true); setCalendarError('');
    try { setCalendars(await api<Calendar[]>('/integrations/google/calendars')); setCalendarsLoaded(true); }
    catch (error) { setCalendarError(explain(error)); await refreshStatus().catch(() => undefined); }
    finally { setBusy(false); }
  }
  async function select(id: string) {
    setBusy(true); setCalendarError(''); setEvents(null);
    try {
      await api('/integrations/google/calendars/select', { calendar_id: id });
      setCalendars(items => items.map(item => ({ ...item, is_selected: item.id === id })));
    } catch (error) {
      setCalendarError(explain(error));
      // Reload permissions, because a previously writable calendar may have changed.
      await api<Calendar[]>('/integrations/google/calendars').then(setCalendars).catch(() => undefined);
      await refreshStatus().catch(() => undefined);
    } finally { setBusy(false); }
  }
  async function loadEvents() {
    setBusy(true); setEventError(''); setEvents(null);
    try { setEvents(await api<Events>('/integrations/google/events')); }
    catch (error) {
      setEventError(explain(error));
      if (error instanceof ApiError && error.status === 403) {
        await api<Calendar[]>('/integrations/google/calendars').then(setCalendars).catch(() => undefined);
      }
      await refreshStatus().catch(() => undefined);
    } finally { setBusy(false); }
  }
  async function saveSheet() {
    setBusy(true); setSheetMessage('');
    try {
      setSheet(await api<SheetConfig>('/integrations/sheets', { spreadsheet_id: spreadsheetId.trim() }, 'PUT'));
      setSheetMessage('Spreadsheet validated and connected.');
    } catch (error) { setSheetMessage(explain(error)); }
    finally { setBusy(false); }
  }
  async function checkSheet() {
    setBusy(true); setSheetMessage('');
    try {
      const health = await api<{ customer_count: number; waiting_list_count: number }>('/integrations/sheets/health');
      setSheetMessage(`Healthy: ${health.customer_count} customers and ${health.waiting_list_count} waiting list entries.`);
    } catch (error) { setSheetMessage(explain(error)); }
    finally { setBusy(false); }
  }
  const connected = connection?.connection_status === 'connected';
  const pending = connection?.connection_status === 'disconnect_pending';
  const manager = business && ['owner', 'admin'].includes(business.role);
  return <section>
    <p className="eyebrow">Business settings{business ? ` / ${business.name}` : ''}</p>
    <h1 className="heading">Google integrations</h1>
    <p className="mt-4 max-w-2xl text-[#5a6f65]">Connect your business account to preview recent inbox messages and choose your appointment calendar.</p>
    {notice && <p role="status" className="mt-6 rounded-lg bg-[#e1e9dc] p-4">{notice}</p>}
    {error && <p role="alert" className="mt-6 text-red-800">{error}</p>}
    {!business && !error && <p className="mt-6" role="status">Loading settings...</p>}
    {business && !manager && <p className="panel mt-6">Google integrations are managed by your business owner or admin.</p>}
    {manager && <>
      <div className="panel mt-8">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div><h2 className="text-xl font-semibold">Google account</h2>
            <p className="mt-3 capitalize">{connection?.connection_status.replaceAll('_', ' ') ?? 'Loading...'}</p>
            {connection?.email && <p className="mt-2 text-sm">{connection.email}</p>}
          </div>
          <div className="flex flex-wrap gap-3">
            {!pending && <button className="primary" disabled={busy || !connection?.configured} onClick={() => void connect()}>
              {connection?.email ? 'Reconnect Google' : 'Connect Google'}</button>}
            {connection && connection.connection_status !== 'disconnected' && <button className="rounded-full border px-5 py-3" disabled={busy} onClick={() => void disconnect()}>
              {pending ? 'Retry Disconnect' : 'Disconnect'}</button>}
          </div>
        </div>
        {connection && !connection.configured && <p className="mt-5 text-sm text-amber-900">Google OAuth and token encryption need server configuration before connecting.</p>}
        {pending && <p className="mt-5 text-sm text-amber-900">Google access is disabled locally. Retry Disconnect to finish revoking the stored token.</p>}
        {connection?.connection_status === 'reconnect_required' && <p className="mt-5 text-sm text-amber-900">Google access has expired or been revoked. Reconnect to restore access.</p>}
        <p className="mt-5 text-sm text-[#5a6f65]">Connecting grants the requested Gmail, Calendar, and Sheets permissions. Previews do not send messages or change events. Reservation metadata is read from existing events; this task does not change events.</p>
        <p className="mt-2 text-sm text-[#5a6f65]">Disconnect revokes this app's Google grant. If the same Google account is connected to another business in this app, it may need reconnecting too.</p>
      </div>
      {connected && <section className="panel mt-6">
        <h2 className="text-xl font-semibold">Customer spreadsheet</h2>
        <p className="mt-3 text-sm">Use a spreadsheet shared with the connected Google account, with Customers and WaitingList worksheets.</p>
        {sheet?.requires_reconsent && <p className="mt-4 text-amber-900">Reconnect Google and approve Sheets access before continuing.</p>}
        <label className="mt-5 block">Spreadsheet ID<input className="field" value={spreadsheetId} onChange={event => setSpreadsheetId(event.target.value)} maxLength={200} placeholder="ID from the Google Sheets URL"/></label>
        <div className="mt-5 flex flex-wrap gap-3">
          <button className="primary" disabled={busy || !spreadsheetId || sheet?.requires_reconsent} onClick={() => void saveSheet()}>Save and validate</button>
          <button className="rounded-full border px-5 py-3" disabled={busy || !sheet?.configured || sheet.requires_reconsent} onClick={() => void checkSheet()}>Check spreadsheet health</button>
        </div>
        {sheetMessage && <p role="status" className="mt-4">{sheetMessage}</p>}
      </section>}
      {connected && <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <section className="panel">
          <h2 className="text-xl font-semibold">Recent inbox messages</h2>
          <p className="mt-3 text-sm text-[#5a6f65]">The latest five INBOX messages. Only sender, subject, and date are displayed.</p>
          <button className="primary mt-5" disabled={busy} onClick={() => void loadMail()}>Load recent emails</button>
          {mailError && <p role="alert" className="mt-4 text-red-800">{mailError}</p>}
          {mail && <><p className="mt-5 text-sm">Mailbox: {mail.email}</p>
            {mail.messages.length === 0 && <p className="mt-4">Your inbox is empty.</p>}
            <ul className="mt-4 divide-y divide-[#d4ddd0]">{mail.messages.map(item => <li key={item.id} className="py-4 break-words">
              <h3 className="font-medium">{item.subject || '(No subject)'}</h3><p className="mt-1 text-sm">{item.sender || 'Unknown sender'}</p><p className="mt-1 text-xs text-[#5a6f65]">{item.date}</p>
            </li>)}</ul></>}
        </section>
        <section className="panel">
          <h2 className="text-xl font-semibold">Business calendar</h2>
          <p className="mt-3 text-sm text-[#5a6f65]">Choose a calendar with owner or writer access. Permissions are checked again when selecting it.</p>
          <button className="primary mt-5" disabled={busy} onClick={() => void loadCalendars()}>Load calendars</button>
          {calendarError && <p role="alert" className="mt-4 text-red-800">{calendarError}</p>}
          {calendarsLoaded && calendars.length === 0 && !calendarError && <p className="mt-4">No accessible calendars were found.</p>}
          <ul className="mt-4 divide-y divide-[#d4ddd0]">{calendars.map(item => <li key={item.id} className="py-4">
            <label className="flex items-start gap-3"><input type="radio" name="calendar" checked={item.is_selected}
              disabled={busy || !['owner', 'writer'].includes(item.access_role)} onChange={() => void select(item.id)} className="mt-1"/>
              <span><span className="font-medium">{item.calendar_name}</span><span className="mt-1 block text-sm text-[#5a6f65]">{item.timezone} / {item.access_role}{item.is_selected ? ' / Selected' : ''}</span></span>
            </label>
          </li>)}</ul>
          <button className="primary mt-5" disabled={busy || !calendars.some(item => item.is_selected)} onClick={() => void loadEvents()}>Preview upcoming events</button>
          {eventError && <p role="alert" className="mt-4 text-red-800">{eventError}</p>}
          {events && <div className="mt-5"><h3 className="font-medium">{events.calendar_name}</h3><p className="text-xs">{events.timezone}</p>
            {events.events.length === 0 && <p className="mt-4">No upcoming events.</p>}
            <ul className="mt-3 divide-y divide-[#d4ddd0]">{events.events.map(item => <li key={item.id} className="py-3">
              <p className="font-medium">{item.summary}</p><p className="mt-1 break-words text-sm">{item.start} - {item.end}</p>
            </li>)}</ul></div>}
        </section>
      </div>}
      {busy && <p role="status" className="mt-5">Working on your request...</p>}
    </>}
    <a href="/businesses" className="mt-8 inline-block underline">Switch business</a>
  </section>;
}
