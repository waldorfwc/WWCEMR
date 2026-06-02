import { Component } from 'react'

/**
 * Catches errors in its children and renders an inline fallback so a
 * single broken panel doesn't blank the whole page. Logs the error +
 * component stack to the browser console for debugging.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, info: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Surface in production console — the message + stack tell us what blew up.
    console.error('[ErrorBoundary]', this.props.label || 'unlabeled',
                  error?.message, error, info?.componentStack)
    this.setState({ info })
  }

  render() {
    if (this.state.error) {
      return (
        <div className="bg-red-50 border border-red-300 rounded p-3 text-[12px] text-red-800 space-y-1">
          <div className="font-semibold">
            ⚠ {this.props.label ? `${this.props.label}: ` : ''}something went wrong rendering this section.
          </div>
          <div className="font-mono break-words text-[11px]">
            {String(this.state.error?.message || this.state.error)}
          </div>
          {this.state.info?.componentStack && (
            <details className="text-[10px] text-red-700">
              <summary className="cursor-pointer">Component stack</summary>
              <pre className="whitespace-pre-wrap">{this.state.info.componentStack}</pre>
            </details>
          )}
          <button
            type="button"
            onClick={() => this.setState({ error: null, info: null })}
            className="text-[11px] text-plum-700 hover:underline"
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
