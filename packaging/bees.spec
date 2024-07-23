Name:           bees
Version:        0.10
Release:        %autorelease
Summary:        Best-Effort Extent-Same, a btrfs dedupe agent

License:        GPL-3.0-only AND MIT AND Zlib
URL:            https://github.com/Zygo/bees

Source:         %{url}/archive/refs/tags/v%{version}.tar.gz

# https://github.com/Zygo/bees/pull/286
Patch0:         286.patch

BuildRequires:  make
BuildRequires:  git
BuildRequires:  gcc-c++
BuildRequires:  btrfs-progs-devel
BuildRequires:  systemd-rpm-macros

%description
bees is a block-oriented userspace deduplication agent designed for large btrfs filesystems. It is an offline dedupe combined with an incremental data scan capability to minimize time data spends on disk from write to dedupe.

%prep
%autosetup -p1

%build
%make_build BEES_VERSION=%{version}

%install
%make_install LIBEXEC_PREFIX=%{_libexecdir}/bees SYSTEMD_SYSTEM_UNIT_DIR=%{_unitdir}

%files
%license COPYING
%doc README.md
%{_libexecdir}/bees/bees
%{_sbindir}/beesd
%{_sysconfdir}/bees/*
%{_unitdir}/beesd@.service

%changelog
%autochangelog