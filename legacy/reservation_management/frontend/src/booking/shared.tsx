import { createContext, useContext } from 'react';
import type { ReactNode } from 'react';
import { api } from '../api';
import type { Business } from '../api';

export type Requirement={ resource_type:string; required_count:number };
export type Policy={ require_resources:boolean; check_in_time:string; check_out_time:string; min_nights:number; max_nights:number|null; turnaround_minutes:number; requirements:Requirement[] };
export type Offering={id:string;name:string;description:string|null;category:string;booking_mode:'TIMED'|'NIGHTLY'|'CAPACITY';default_duration_minutes:number|null;min_duration_minutes:number|null;max_duration_minutes:number|null;capacity_limit:number|null;active:boolean;policy:Policy};
export type Resource={id:string;name:string;resource_type:string;capacity:number|null;allocation_mode:'EXCLUSIVE'|'SHARED';active:boolean};
export type Customer={id:string;full_name:string;email:string;phone:string|null;notification_consent:boolean};
export type Compatibility={id:string;offering_id:string;resource_id:string};
export type Reservation={id:string;customer_id:string;offering_id:string;start_at:string;end_at:string;local_start_date:string|null;local_end_date:string|null;party_size:number|null;quantity:number;status:string;resource_ids:string[];calendar_sync_status:string;sync_version:number;google_calendar_id:string|null;google_event_id:string|null};
export type Waitlist={id:string;customer_id:string;offering_id:string;available_from:string;available_until:string;local_start_date:string|null;local_end_date:string|null;party_size:number|null;quantity:number;preferred_resource_id:string|null;status:string};
export type Workspace={business:Business;offerings:Offering[];resources:Resource[];customers:Customer[];compatibility:Compatibility[];reload:()=>Promise<void>};
export const WorkspaceContext=createContext<Workspace|null>(null);
export function useWorkspace(){const context=useContext(WorkspaceContext);if(!context)throw new Error('Workspace not loaded');return context;}
export const endpoint='/booking';
export const send=<T,>(path:string,body?:unknown,method?:'GET'|'POST'|'PUT'|'DELETE')=>api<T>(endpoint+path,body,method);
export const explain=(error:unknown)=>error instanceof Error?error.message:'Please retry.';
export const num=(data:FormData,key:string)=>String(data.get(key)??'').trim()?Number(data.get(key)):null;
export const text=(data:FormData,key:string)=>String(data.get(key)??'').trim();
export function Field({label,children}:{label:string;children:ReactNode}){return <label className="block text-sm font-medium">{label}{children}</label>;}
export function Notice({error}:{error:string}){return error?<p role="alert" className="my-4 rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>:null;}
export function localDateTime(value:string,zone:string){const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value));const get=(name:string)=>parts.find(part=>part.type===name)?.value;return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`;}
export function dateTime(value:string,zone:string){return new Intl.DateTimeFormat('en',{timeZone:zone,dateStyle:'medium',timeStyle:'short'}).format(new Date(value));}
export function Table({children}:{children:ReactNode}){return <div className="mt-6 overflow-x-auto rounded-xl border border-[#d4ddd0]"><table className="w-full text-left text-sm [&_th]:bg-[#e1e9dc] [&_th]:p-3 [&_td]:border-t [&_td]:border-[#d4ddd0] [&_td]:p-3">{children}</table></div>;}
