Name:           bees
Version:        0.10
Release:        1%{?dist}
Summary:        Best-Effort Extent-Same, a btrfs dedupe agent

License:        GPLv3
URL:            https://github.com/Zygo/bees

Source:         %{url}/archive/refs/tags/v%{version}.tar.gz

Patch0:         0001-Fix-compilation-error-with-GCC14.patch

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
%set_build_flags
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
* Tue July 23 2024 - Kyle Gospodnetich <kylego@microsoft.com> - 0.10-1
- Initial build