import SlotMachine from './slots/SlotMachine';

function App() {
  return (
    <div className="min-h-screen w-full bg-slate-950 bg-[radial-gradient(ellipse_at_top,_rgba(180,83,9,0.15),_transparent_55%)]">
      <div className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center py-6">
        <SlotMachine />
      </div>
      <footer className="pb-6 text-center text-[10px] text-slate-700">
        For entertainment only · No real money
      </footer>
    </div>
  );
}

export default App;
