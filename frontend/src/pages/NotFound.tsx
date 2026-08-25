import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router-dom'

export function NotFound() {
  return <div className="not-found"><span>404</span><h1>Page not found</h1><p>The page may have moved or no longer exists.</p><Link className="button button-primary" to="/"><ArrowLeft size={16} /> Back to overview</Link></div>
}
