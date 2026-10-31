import { useCallback, useEffect, useState } from 'react';
import { api, ApiError } from '../api';
import type { Business } from '../api';
import { WorkspaceContext, explain, send } from './shared';
import type { Offering, Resource, Customer, Compatibility, Workspace } from './shared';
import { OfferingsPanel, ResourcesPanel, CustomersPanel } from './CatalogPanels';
import { CalendarPanel, ReservationDetails, ReservationEditor, ReservationsPanel } from './ReservationPanels';
import { BusinessSettings, SyncPanel, WaitlistPanel } from './OtherPanels';

export function BookingWorkspace(){const[data,setData]=useState<Omit<Workspace,'reload'>|null>(null);const[error,setError]=useState('');const path=window.location.pathname.split('/').filter(Boolean);const section=path[1]??'reservations';
 const reload=useCallback(async()=>{const[business,offerings,resources,customers,compatibility]=await Promise.all([api<Business>('/dashboard'),send<Offering[]>('/offerings?limit=500'),send<Resource[]>('/resources?limit=500'),send<Customer[]>('/customers?limit=500'),send<Compatibility[]>('/offering-resources?limit=500')]);setData({business,offerings,resources,customers,compatibility})},[]);
 useEffect(()=>{void reload().catch(error=>{if(error instanceof ApiError&&[404,409].includes(error.status))window.location.assign('/businesses');else setError(explain(error))})},[reload]);
 if(error)return <p role="alert">{error}</p>;if(!data)return <p role="status">Loading reservation workspace...</p>;
 return <WorkspaceContext.Provider value={{...data,reload}}><div className="mb-8 border-b border-[#cad5ca] pb-5"><p className="eyebrow">{data.business.name} / {data.business.timezone}</p><nav aria-label="Reservation workspace" className="mt-5 flex flex-wrap gap-3">{[['reservations','Reservations'],['calendar','Calendar'],['offerings','Offerings'],['resources','Resources'],['customers','Customers'],['waitlist','Waiting list'],['sync','Calendar sync'],['settings','Business settings']].map(([key,label])=><a key={key} href={'/manage/'+key} aria-current={section===key?'page':undefined} className={'rounded-full px-4 py-2 text-sm '+(section===key?'bg-[#183f35] text-white':'border border-[#cad5ca]')}>{label}</a>)}</nav></div>
 {section==='offerings'?<OfferingsPanel/>:section==='resources'?<ResourcesPanel/>:section==='customers'?<CustomersPanel/>:section==='calendar'?<CalendarPanel/>:section==='waitlist'?<WaitlistPanel/>:section==='settings'?<BusinessSettings/>:section==='sync'?<SyncPanel/>:section==='reservations'&&path[2]==='new'?<ReservationEditor/>:section==='reservations'&&path[2]?<ReservationDetails id={path[2]}/>:<ReservationsPanel/>}
 </WorkspaceContext.Provider>}
