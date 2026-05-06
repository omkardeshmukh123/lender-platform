'use client'

import React from 'react'

interface Props {
  children: React.ReactNode
  label?: string
  fallback?: React.ReactNode
}

interface State { hasError: boolean }

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[ErrorBoundary:${this.props.label ?? 'unknown'}]`, error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div
          className="rounded-xl border px-5 py-4 text-sm"
          style={{ borderColor: '#E6F4F4', background: '#F7FAFA', color: '#7A9E9E' }}
        >
          Something went wrong loading this section.{' '}
          <button
            className="underline font-medium"
            style={{ color: '#1A7070' }}
            onClick={() => this.setState({ hasError: false })}
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
