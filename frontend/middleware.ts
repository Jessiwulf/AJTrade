import { NextRequest, NextResponse } from 'next/server'

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'

async function getAdminSessionRole(request: NextRequest) {
  const session = request.cookies.get('session')?.value
  if (!session) {
    return null
  }

  const response = await fetch(`${BACKEND_URL}/api/auth/me`, {
    headers: {
      Authorization: `Bearer ${session}`,
    },
    cache: 'no-store',
  })

  if (!response.ok) {
    return null
  }

  const data = await response.json()
  return data?.user?.role || null
}

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl

  if (!pathname.startsWith('/admin')) {
    return NextResponse.next()
  }

  const role = await getAdminSessionRole(request)
  if (role !== 'admin') {
    const loginUrl = new URL('/login', request.url)
    loginUrl.searchParams.set('next', `${pathname}${search}`)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/admin/:path*'],
}