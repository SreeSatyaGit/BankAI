const VARIANT = {
  idle: "lamp-idle",
  success: "lamp-success",
  info: "lamp-info",
  danger: "lamp-danger",
  attn: "lamp-attn",
};

export default function Lamp({ variant = "idle", on = true, className = "" }) {
  const cls = ["lamp", VARIANT[variant] || VARIANT.idle, on ? "on" : "", className]
    .filter(Boolean)
    .join(" ");
  return <span className={cls} />;
}
