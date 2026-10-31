export function Landing() {
  return (
    <div className="min-h-screen bg-[#f5f5ef] text-[#183f35]">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-7">
        <a href="#" className="text-2xl font-bold tracking-tight" aria-label="ReSlot AI home">ReSlot <span className="text-[#658476]">AI</span></a>
        <a href="/login" className="rounded-full border border-[#cad5ca] px-4 py-2 text-sm font-medium">Log in ?</a>
      </header>
      <main className="mx-auto max-w-6xl px-6 pb-16 pt-16 md:pt-24">
        <div className="grid items-center gap-14 md:grid-cols-2">
          <section>
            <p className="mb-6 text-sm font-semibold uppercase tracking-[0.22em]">Less downtime. More possibility.</p>
            <h1 className="text-5xl font-medium leading-[1.06] tracking-tight md:text-7xl">A cancelled slot.<br/><span className="text-[#71917c]">A fresh start.</span></h1>
            <p className="mt-7 max-w-md text-lg leading-relaxed text-[#5a6f65]">Make room for what comes next. ReSlot AI is being built to help reservation-based businesses turn last-minute cancellations into new opportunities.</p>
            <a href="/register" className="mt-9 inline-block rounded-full bg-[#183f35] px-7 py-4 font-medium text-white hover:bg-[#2b594b] focus-visible:outline-2 focus-visible:outline-offset-4">Create your account <span aria-hidden="true">?</span></a>
            <p className="mt-5 text-xs text-[#5a6f65]">In development ? Built for reservation-based businesses</p>
          </section>
          <aside aria-label="Illustration of a future appointment recovery" className="rounded-[2rem] bg-[#e1e9dc] p-8 md:p-12">
            <p className="mb-10 text-xs font-semibold uppercase tracking-widest">A little space for opportunity</p>
            <div className="rotate-[-3deg] rounded-2xl bg-white p-6 shadow-sm">
              <div className="flex justify-between text-sm"><span>Thursday, 14:00</span><span className="text-[#74816f]">Illustration</span></div>
              <div className="my-6 border-t border-dashed border-[#cad5ca]"/>
              <p className="text-2xl font-medium">Your next opening</p>
              <p className="mt-2 text-sm text-[#5a6f65]">An appointment worth filling.</p>
              <div className="mt-7 rounded-lg bg-[#eef3e9] px-4 py-3 text-sm">Cancellation ? Opportunity</div>
            </div>
            <p className="mt-12 text-sm leading-relaxed text-[#5a6f65]">Thoughtful automation.<br/>More time for your customers.</p>
          </aside>
        </div>
        <section id="how-it-works" className="mt-24 border-t border-[#cad5ca] pt-10">
          <p className="text-sm uppercase tracking-widest">The vision</p>
          <div className="mt-8 grid gap-8 md:grid-cols-3">
            {[
              ['01', 'Notice the opening', 'Read cancellation signals from existing Gmail and Calendar accounts.'],
              ['02', 'Find the right fit', 'Use customer consent and waiting lists from an authorized Google Sheet.'],
              ['03', 'Bring it together', 'Prepare for specialized agents to coordinate replacement offers.'],
            ].map(([number, title, description]) => <article key={number}><span className="text-sm text-[#71917c]">{number}</span><h2 className="mt-3 text-xl font-medium">{title}</h2><p className="mt-3 text-sm leading-relaxed text-[#5a6f65]">{description}</p></article>)}
          </div>
          <p className="mt-10 text-xs text-[#5a6f65]">Google integrations are available. Automated cancellation recovery and AI agents are planned capabilities.</p>
        </section>
      </main>
      <footer className="mx-auto max-w-6xl px-6 py-8 text-xs text-[#5a6f65]">ReSlot AI ? Make every opening count.</footer>
    </div>
  );
}

