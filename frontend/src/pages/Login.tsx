import { FlaskConical, LockKeyhole } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'

export function Login() {
  const auth = useAuth()
  const [error, setError] = useState<string | null>(null)
  const [configurationError, setConfigurationError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [bootstrapRequired, setBootstrapRequired] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    api.bootstrapStatus()
      .then(setBootstrapRequired)
      .catch(reason => setConfigurationError(reason instanceof Error ? reason.message : 'Could not reach the Spectarr API'))
      .finally(() => setChecking(false))
  }, [])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setError(null)
    setSubmitting(true)
    try {
      const username = String(form.get('username'))
      const password = String(form.get('password'))
      if (bootstrapRequired) await auth.bootstrap(username, password)
      else await auth.login(username, password)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : bootstrapRequired ? 'Setup failed' : 'Sign in failed')
    } finally {
      setSubmitting(false)
    }
  }

  return <main className="login-page">
    <section className="login-card">
      <div className="login-brand"><span><FlaskConical size={25} /></span><div><strong>Spectarr</strong><small>Mass spec library</small></div></div>
      <div className="login-copy"><LockKeyhole size={21} /><h1>{bootstrapRequired ? 'Create the first admin' : 'Sign in'}</h1><p>{bootstrapRequired ? 'Secure this new Spectarr instance with a local administrator account.' : 'Use your local Spectarr account.'}</p></div>
      {!checking && !configurationError && <form onSubmit={event => void submit(event)}>
        <label><span>Username</span><input name="username" required autoComplete="username" autoFocus /></label>
        <label><span>Password</span><input name="password" type="password" required minLength={bootstrapRequired ? 12 : undefined} autoComplete={bootstrapRequired ? 'new-password' : 'current-password'} /></label>
        <button className="button button-primary" type="submit" disabled={submitting}>{submitting ? bootstrapRequired ? 'Creating admin' : 'Signing in' : bootstrapRequired ? 'Create admin' : 'Sign in'}</button>
      </form>}
      {checking && <div className="settings-placeholder">Checking the Spectarr API</div>}
      {configurationError && <div className="modal-error login-error" role="alert">{configurationError}</div>}
      {error && <div className="modal-error login-error" role="alert">{error}</div>}
    </section>
  </main>
}
