import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import Modal from './Modal'

/**
 * The account control, and the sheet it opens.
 *
 * Everything here belongs to the person rather than the clinic: their photo, what they are
 * called, and the way out. Sign-out lives behind the menu rather than sitting on the top
 * bar, so it stops being a thing you hit on the way to something else.
 */
export default function ProfileMenu() {
  const { user, signOut, refresh } = useAuth()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const container = useRef(null)

  // Close on an outside click or on Escape — a dropdown that only closes by clicking its
  // own button is one people leave open and then click through.
  useEffect(() => {
    if (!open) return

    const away = (e) => { if (!container.current?.contains(e.target)) setOpen(false) }
    const escape = (e) => { if (e.key === 'Escape') setOpen(false) }

    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div className="profile-container" ref={container} style={{ position: 'relative' }}>
      <button
        className={`profile-btn${open ? ' open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <Avatar user={user} />
        <span className="who">
          <strong>{user.name}</strong>
          <span className="muted small">{user.title || user.role}</span>
        </span>
        <span className="caret" aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="profile-sheet" role="menu">
          <div className="head">
            <Avatar user={user} />
            <div>
              <strong>{user.name}</strong>
              <span className="muted small">{user.email}</span>
            </div>
          </div>

          <button className="sheet-item" onClick={() => { setEditing(true); setOpen(false) }}>
            <span aria-hidden="true">◔</span> Edit profile
          </button>

          <button className="sheet-item danger" onClick={signOut}>
            <span aria-hidden="true">⏻</span> Sign out
          </button>
        </div>
      )}

      {editing && (
        <Modal title="Your profile" onClose={() => setEditing(false)}>
          <ProfileForm
            user={user}
            onCancel={() => setEditing(false)}
            onSaved={async () => { await refresh(); setEditing(false) }}
          />
        </Modal>
      )}
    </div>
  )
}

/** Their photo, or their initials. Never a broken image icon. */
export function Avatar({ user, large = false }) {
  const initials = (user.name || '?')
    .replace(/^Dr\.?\s+/i, '')
    .split(' ').map((w) => w[0]).slice(0, 2).join('').toUpperCase()

  if (user.avatar) {
    return <img className={`avatar-img${large ? ' lg' : ''}`} src={user.avatar} alt="" />
  }

  return (
    <span className="avatar" style={large ? { width: 84, height: 84, borderRadius: 22, fontSize: '1.6rem' } : undefined}>
      {initials}
    </span>
  )
}

/**
 * Editing your own presentation.
 *
 * The photo is downscaled to 256px in the browser before it is ever sent. A phone camera
 * produces four megabytes; a face at the size it is actually displayed needs about thirty
 * kilobytes, and shipping the difference to store it in a column would be silly.
 */
function ProfileForm({ user, onSaved, onCancel }) {
  const [form, setForm] = useState({
    name: user.name ?? '',
    title: user.title ?? '',
    avatar: user.avatar ?? null,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const file = useRef(null)

  const pick = async (e) => {
    const chosen = e.target.files?.[0]
    if (!chosen) return

    setError(null)

    if (!/^image\/(png|jpe?g|webp)$/.test(chosen.type)) {
      setError('Choose a PNG, JPEG or WebP image.')
      return
    }

    try {
      // Resolved before the updater, not inside it — a state updater is called
      // synchronously and cannot be awaited in.
      const avatar = await downscale(chosen)
      setForm((f) => ({ ...f, avatar }))
    } catch {
      setError('That image could not be read.')
    }
  }

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.updateProfile({ name: form.name, title: form.title || null, avatar: form.avatar })
      onSaved()
    } catch (err) {
      setError(err.body?.errors ? Object.values(err.body.errors)[0][0] : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      {error && <div className="note bad">{error}</div>}

      <div className="avatar-editor">
        <Avatar user={{ ...user, ...form }} large />

        <div className="actions">
          <button type="button" onClick={() => file.current?.click()}>Change photo</button>
          {form.avatar && (
            <button type="button" className="danger"
              onClick={() => setForm((f) => ({ ...f, avatar: null }))}>
              Remove photo
            </button>
          )}
          <span className="muted small">PNG, JPEG or WebP</span>
        </div>

        {/* Hidden, and driven by the button above: the native control cannot be made to
            match the rest of the form, and a picker is not what people want to look at. */}
        <input ref={file} type="file" accept="image/png,image/jpeg,image/webp"
          onChange={pick} style={{ display: 'none' }} />
      </div>

      <label>Display name
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </label>

      <label>Title
        <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
          placeholder="Respiratory Consultant" />
      </label>
      <p className="muted small" style={{ marginTop: -6 }}>
        Shown beside your name. Your role and permissions are the administrator's to set.
      </p>

      <div className="form-actions">
        <button className="primary" disabled={busy || !form.name.trim()}>
          {busy ? 'Saving…' : 'Save profile'}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  )
}

/**
 * Shrink an image to a square data URL, cropping to the centre.
 *
 * Centre-crop rather than squash: a face in a 4:3 photo squeezed into a circle looks
 * wrong in a way people notice without being able to say why.
 */
function downscale(file, size = 256) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.onerror = reject
    reader.onload = () => {
      const image = new Image()

      image.onerror = reject
      image.onload = () => {
        const canvas = document.createElement('canvas')
        canvas.width = canvas.height = size

        const side = Math.min(image.width, image.height)
        const context = canvas.getContext('2d')

        context.drawImage(
          image,
          (image.width - side) / 2, (image.height - side) / 2, side, side,
          0, 0, size, size,
        )

        resolve(canvas.toDataURL('image/jpeg', 0.85))
      }

      image.src = reader.result
    }

    reader.readAsDataURL(file)
  })
}
