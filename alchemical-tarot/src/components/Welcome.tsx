export function Welcome({ onEnter }: { onEnter: () => void }) {
  return (
    <div className="welcome-screen">
      <div className="alchemical-seal">🜍</div>
      <h1 style={{ fontSize: '2rem' }}>Alchemical Tarot</h1>
      <p style={{ maxWidth: 340 }}>
        A quiet companion for the work you already do — sun, moon, and the
        turning of the cards. Solve et coagula: dissolve what no longer
        serves, and let what remains take shape.
      </p>
      <div className="glyph-row">
        <span>☉</span><span>☽</span><span>🜁</span><span>🜂</span><span>🜃</span><span>🜄</span>
      </div>
      <button className="btn btn-primary" onClick={onEnter}>Enter</button>
    </div>
  );
}
