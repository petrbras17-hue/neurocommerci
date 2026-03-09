type PlaceholderPageProps = {
  title: string;
  description: string;
};

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <div className="eyebrow">Shell page</div>
          <h2>{title}</h2>
        </div>
      </div>
      <p className="muted">{description}</p>
      <div className="placeholder-grid">
        <div className="placeholder-card">Status cards</div>
        <div className="placeholder-card">Usage snapshot</div>
        <div className="placeholder-card">Next sprint surface</div>
      </div>
    </section>
  );
}
