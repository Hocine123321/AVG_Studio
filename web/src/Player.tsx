export interface PlayerProps {
  /** Full URL to `GET /api/projects/{id}/video`, already carrying the
   * session token as a `?token=` query param — a plain `<video>` element
   * issues its own request and can't attach the `X-AVG-Token` header
   * `api.ts` uses everywhere else (see `avg/api/deps.py`'s docstring). */
  src: string;
}

/**
 * The inline video player. Unchanged in behaviour — the `/video` route
 * honours `Range` and answers a real 206, so the browser's own seek bar
 * works with no plumbing here. Only the surround changed: `.player` is now
 * an over-rounded well on near-black, so the frame reads as the object and
 * the chrome disappears around it.
 */
export default function Player({ src }: PlayerProps) {
  return (
    <div className="player">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- legacy renders carry no caption track to attach */}
      <video controls preload="metadata" src={src} />
    </div>
  );
}
